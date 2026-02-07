from sql.psql_client import PSQLClient
from datetime import datetime, timedelta, timezone
from util.fcr.file_config_reader import FileConfigReader, ConfTypes

fcr = FileConfigReader()
metrics_db_config = fcr.load_config(
	"src/config/metrics_db.conf",
	conf_type=ConfTypes.KEY_VALUE,
	required_keys=["database", "user", "password"],
)

metrics_db = PSQLClient(
	database=metrics_db_config["database"],
	user=metrics_db_config["user"],
	password=metrics_db_config["password"],
	host=metrics_db_config.get("host", "localhost"),
)

METRICS_TABLE = "server_metrics"
METRICS_NAMES = {
	"cpu_used": "CPU Used",
	"cpu_temp": "CPU Temperature",
	"ram_used": "RAM Used",
	"disk_used": "Disk Used",
	"net_up": "Network Upload",
	"net_dn": "Network Download",
}
METRICS_UNITS = {
	"cpu_used": "%",
	"ram_used": "%",
	"disk_used": "%",
	"cpu_temp": "°C",
	"net_up": "B/s",
	"net_dn": "B/s",
}


def _get_latest_metrics(num_entries: int = 720):
    rows, _ = metrics_db.get_rows_with_filters(
        METRICS_TABLE,
        order_by="ts",
        order_dir="DESC",
        page_limit=num_entries,
    )
    rows = rows[::-1]

    return rows

def get_metrics(metric: str, num_entries: int = 720, format_ts: bool = False):
	if metric not in METRICS_NAMES:
		raise ValueError(f"Metric '{metric}' is not recognized.")

	rows = _get_latest_metrics(num_entries)
	if not rows:
		return [], []

	if format_ts:
		timestamps = [r["ts"].strftime("%Y-%m-%d %H:%M:%S") for r in rows]
	else:
		timestamps = [r["ts"] for r in rows]

	values = [r[metric] for r in rows]

	return timestamps, values


def _get_ts_expr_and_where(ts_type: str, ts_udt: str, since_dt: datetime):
	is_ts = ts_type in {"timestamp", "timestamp without time zone"} or ts_udt in {"timestamp"}
	is_tstz = ts_type in {"timestamp with time zone"} or ts_udt in {"timestamptz"}
	is_bigint = ts_type in {"bigint"} or ts_udt in {"int8"}

	if is_bigint:
		where_expr = "ts >= %s"
		since_param = int(since_dt.timestamp() * 1000)
		ts_expr = "to_timestamp(ts / 1000.0)"
	elif is_ts:
		where_expr = "ts >= %s"
		since_param = since_dt.replace(tzinfo=None)
		ts_expr = "ts AT TIME ZONE 'UTC'"
	else:
		where_expr = "ts >= %s"
		since_param = since_dt
		ts_expr = "ts"

	return ts_expr, where_expr, since_param


def _get_metrics_bucketed_downsample(
	metric: str,
	*,
	since_dt: datetime,
	bucket_seconds: int,
	format_ts: bool = False,
):
	col_info = metrics_db.get_column_info("public", METRICS_TABLE)
	ts_col = col_info.get("ts", {})
	ts_type = (ts_col.get("data_type") or "").lower()
	ts_udt = (ts_col.get("udt_name") or "").lower()

	ts_expr, where_expr, since_param = _get_ts_expr_and_where(ts_type, ts_udt, since_dt)

	count_rows = metrics_db.execute_query(
		f"SELECT COUNT(*) AS cnt FROM {METRICS_TABLE} WHERE {where_expr};",
		[since_param],
	) or []
	if not count_rows:
		return [], []
	total = int(count_rows[0].get("cnt", 0) or 0)
	if total == 0:
		return [], []

	window_seconds = max(1, int((datetime.now(timezone.utc) - since_dt).total_seconds()))
	target_points = max(2, int(window_seconds / bucket_seconds))
	step = max(1, total // target_points)

	query = f"""
		WITH ordered AS (
			SELECT {ts_expr} AS ts,
				{metric} AS value,
				row_number() OVER (ORDER BY {ts_expr}) AS rn
			FROM {METRICS_TABLE}
			WHERE {where_expr}
		)
		SELECT ts, value
		FROM ordered
		WHERE (rn % %s) = 0
		ORDER BY ts ASC;
	"""
	rows = metrics_db.execute_query(query, [since_param, step]) or []
	if not rows:
		return [], []

	if format_ts:
		timestamps = [r["ts"].strftime("%Y-%m-%d %H:%M:%S") for r in rows]
	else:
		timestamps = [r["ts"] for r in rows]
	values = [r["value"] for r in rows]
	return timestamps, values


def get_metrics_bulk(
	metrics: list[str],
	*,
	num_entries: int = 720,
	format_ts: bool = False,
):
	if not metrics:
		return [], {}
	unknown = [m for m in metrics if m not in METRICS_NAMES]
	if unknown:
		raise ValueError(f"Metrics not recognized: {', '.join(unknown)}")

	rows = _get_latest_metrics(num_entries)
	if not rows:
		return [], {m: [] for m in metrics}

	if format_ts:
		timestamps = [r["ts"].strftime("%Y-%m-%d %H:%M:%S") for r in rows]
	else:
		timestamps = [r["ts"] for r in rows]

	data = {m: [] for m in metrics}
	for r in rows:
		for m in metrics:
			data[m].append(r.get(m))

	return timestamps, data


def _get_metrics_bucketed_aggregate(
	metric: str,
	*,
	since_dt: datetime,
	bucket_seconds: int,
	format_ts: bool = False,
):
	col_info = metrics_db.get_column_info("public", METRICS_TABLE)
	ts_col = col_info.get("ts", {})
	ts_type = (ts_col.get("data_type") or "").lower()
	ts_udt = (ts_col.get("udt_name") or "").lower()
	is_bigint = ts_type in {"bigint"} or ts_udt in {"int8"}

	bucket_minutes = max(1, bucket_seconds // 60)

	if is_bigint:
		bucket_ms = bucket_seconds * 1000
		bucket_expr = f"to_timestamp(floor(ts / {bucket_ms}) * {bucket_ms} / 1000.0)"
		where_expr = "ts >= %s"
		since_param = int(since_dt.timestamp() * 1000)
	else:
		bucket_expr = None
		where_expr = "ts >= %s"
		since_param = since_dt

	if bucket_expr is None:
		ts_expr = "ts"
		if bucket_seconds % 3600 == 0:
			bucket_expr = f"date_trunc('hour', {ts_expr})"
		elif bucket_minutes == 1:
			bucket_expr = f"date_trunc('minute', {ts_expr})"
		else:
			bucket_expr = (
				f"date_trunc('hour', {ts_expr}) + "
				f"floor(date_part('minute', {ts_expr}) / {bucket_minutes}) * interval '1 minute'"
			)

	query = f"""
		SELECT {bucket_expr} AS bucket, AVG({metric}) AS value
		FROM {METRICS_TABLE}
		WHERE {where_expr}
		GROUP BY bucket
		ORDER BY bucket ASC;
	"""
	rows = metrics_db.execute_query(query, [since_param]) or []
	if not rows:
		return [], []

	if format_ts:
		timestamps = [r["bucket"].strftime("%Y-%m-%d %H:%M:%S") for r in rows]
	else:
		timestamps = [r["bucket"] for r in rows]
	values = [r["value"] for r in rows]
	return timestamps, values


def get_metrics_bucketed(
	metric: str,
	*,
	since_dt: datetime,
	bucket_seconds: int,
	format_ts: bool = False,
):
	if metric not in METRICS_NAMES:
		raise ValueError(f"Metric '{metric}' is not recognized.")
	if bucket_seconds <= 0:
		raise ValueError("bucket_seconds must be positive.")

	bucket_seconds = int(bucket_seconds)

	if bucket_seconds < 60 or bucket_seconds % 60 != 0:
		raise ValueError("bucket_seconds must be a multiple of 60.")
	if bucket_seconds == 10800:
		try:
			return _get_metrics_bucketed_downsample(
				metric,
				since_dt=since_dt,
				bucket_seconds=bucket_seconds,
				format_ts=format_ts,
			)
		except Exception:
			return _get_metrics_bucketed_aggregate(
				metric,
				since_dt=since_dt,
				bucket_seconds=bucket_seconds,
				format_ts=format_ts,
			)
	if bucket_seconds >= 3600:
		return _get_metrics_bucketed_aggregate(
			metric,
			since_dt=since_dt,
			bucket_seconds=bucket_seconds,
			format_ts=format_ts,
		)

	return _get_metrics_bucketed_aggregate(
		metric,
		since_dt=since_dt,
		bucket_seconds=bucket_seconds,
		format_ts=format_ts,
	)
