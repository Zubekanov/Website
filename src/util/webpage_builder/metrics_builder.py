from sql.psql_client import PSQLClient
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
	"ram_used": "RAM Used",
	"disk_used": "Disk Used",
	"cpu_temp": "CPU Temperature",
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
