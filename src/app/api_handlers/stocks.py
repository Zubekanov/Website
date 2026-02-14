from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

import flask

from app.api_context import ApiContext
from sql.psql_client import PSQLClient
from util.fcr.file_config_reader import FileConfigReader, ConfTypes

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DEFAULT_ALPACA_TABLE_CONF = {
	"ALPACA_EQUITIES_TABLE": "public.equities",
	"ALPACA_STOCK_BARS_TABLE": "public.stock_bars",
	"ALPACA_STOCK_DEFAULT_TIMEFRAME": "5Min",
	"ALPACA_STOCK_ROLLING_WINDOW_DAYS": "7",
	"ALPACA_EQUITIES_SYMBOL_COLUMN": "symbol",
	"ALPACA_EQUITIES_NAME_COLUMN": "name",
	"ALPACA_EQUITIES_CLASS_COLUMN": "class",
	"ALPACA_EQUITIES_EXCHANGE_COLUMN": "exchange",
	"ALPACA_EQUITIES_STATUS_COLUMN": "status",
	"ALPACA_EQUITIES_TRADABLE_COLUMN": "tradable",
	"ALPACA_STOCK_BARS_SYMBOL_COLUMN": "symbol",
	"ALPACA_STOCK_BARS_TS_COLUMN": "ts",
	"ALPACA_STOCK_BARS_TIMEFRAME_COLUMN": "timeframe",
	"ALPACA_STOCK_BARS_OPEN_COLUMN": "open",
	"ALPACA_STOCK_BARS_HIGH_COLUMN": "high",
	"ALPACA_STOCK_BARS_LOW_COLUMN": "low",
	"ALPACA_STOCK_BARS_CLOSE_COLUMN": "close",
	"ALPACA_STOCK_BARS_VOLUME_COLUMN": "volume",
	"ALPACA_STOCK_BARS_TRADE_COUNT_COLUMN": "trade_count",
	"ALPACA_STOCK_BARS_VWAP_COLUMN": "vwap",
}

_ALPACA_DB_CLIENT: PSQLClient | None = None
_ALPACA_DB_CLIENT_INIT_ERR: str | None = None


def _quote_ident(name: str) -> str:
	if not _IDENT_RE.fullmatch(name):
		raise ValueError(f"Invalid identifier: {name!r}")
	return f'"{name}"'


def _quote_table(name: str) -> str:
	parts = [p.strip() for p in str(name).split(".") if p.strip()]
	if len(parts) == 1:
		return _quote_ident(parts[0])
	if len(parts) == 2:
		return f"{_quote_ident(parts[0])}.{_quote_ident(parts[1])}"
	raise ValueError(f"Invalid table name: {name!r}")


def _load_alpaca_table_conf(ctx: ApiContext) -> dict[str, str]:
	conf = dict(_DEFAULT_ALPACA_TABLE_CONF)
	try:
		loaded = ctx.fcr.find("alpaca_tables.conf")
		if isinstance(loaded, dict):
			for k in _DEFAULT_ALPACA_TABLE_CONF:
				v = loaded.get(k)
				if v:
					conf[k] = str(v).strip()
	except Exception:
		pass
	return conf


def _parse_iso_or_date(value: str) -> datetime:
	v = (value or "").strip()
	if not v:
		raise ValueError("Empty datetime value.")
	if "T" not in v:
		return datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=timezone.utc)
	if v.endswith("Z"):
		v = v[:-1] + "+00:00"
	dt = datetime.fromisoformat(v)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=timezone.utc)
	return dt.astimezone(timezone.utc)


def _range_delta(range_key: str) -> timedelta | None:
	r = (range_key or "").strip().upper()
	if r == "1D":
		return timedelta(days=1)
	if r == "3D":
		return timedelta(days=3)
	if r == "1W":
		return timedelta(weeks=1)
	if r == "1M":
		return timedelta(days=30)
	if r == "3M":
		return timedelta(days=90)
	if r == "6M":
		return timedelta(days=180)
	if r == "YTD":
		now = datetime.now(timezone.utc)
		return now - now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
	if r == "1Y":
		return timedelta(days=365)
	if r == "5Y":
		return timedelta(days=365 * 5)
	if r == "MAX":
		return None
	return timedelta(days=30)


def _prev_market_day(d):
	cur = d
	while cur.weekday() >= 5:
		cur = cur - timedelta(days=1)
	return cur


def _start_for_1d_window(end_dt: datetime) -> datetime:
	"""
	1D window = last full market-open day + any data from today.
	Market day is interpreted in America/New_York.
	"""
	market_tz = ZoneInfo("America/New_York")
	end_local = end_dt.astimezone(market_tz)
	today = end_local.date()

	# Last full open day before "today" (or Friday when today is weekend).
	last_full_open = _prev_market_day(today - timedelta(days=1))
	start_local = datetime.combine(last_full_open, datetime.min.time(), tzinfo=market_tz)
	return start_local.astimezone(timezone.utc)


def _to_iso(value) -> str | None:
	if value is None:
		return None
	if isinstance(value, datetime):
		if value.tzinfo is None:
			value = value.replace(tzinfo=timezone.utc)
		else:
			value = value.astimezone(timezone.utc)
		return value.isoformat().replace("+00:00", "Z")
	return str(value)


def _get_alpaca_db_client(ctx: ApiContext):
	"""
	Returns a DB client for Alpaca market data.
	Test hook: if ctx has `alpaca_db_client`, use it.
	"""
	test_client = getattr(ctx, "alpaca_db_client", None)
	if test_client is not None:
		return test_client, None

	global _ALPACA_DB_CLIENT, _ALPACA_DB_CLIENT_INIT_ERR
	if _ALPACA_DB_CLIENT is not None:
		return _ALPACA_DB_CLIENT, None
	if _ALPACA_DB_CLIENT_INIT_ERR:
		return None, _ALPACA_DB_CLIENT_INIT_ERR

	try:
		fcr = FileConfigReader()
		cfg = fcr.load_config(
			"src/config/alpaca_db.conf",
			conf_type=ConfTypes.KEY_VALUE,
			required_keys=["database", "user", "password"],
		)
		_ALPACA_DB_CLIENT = PSQLClient(
			database=cfg["database"],
			user=cfg["user"],
			password=cfg["password"],
			host=cfg.get("host", "localhost"),
			port=int(cfg["port"]) if cfg.get("port") else None,
		)
		return _ALPACA_DB_CLIENT, None
	except Exception as exc:
		_ALPACA_DB_CLIENT_INIT_ERR = (
			"Alpaca DB config unavailable. Create src/config/alpaca_db.conf "
			"with database/user/password (and optional host/port)."
		)
		return None, _ALPACA_DB_CLIENT_INIT_ERR


def _is_missing_relation_error(exc: Exception) -> bool:
	cur = exc
	visited = set()
	while cur and id(cur) not in visited:
		visited.add(id(cur))
		pgcode = getattr(cur, "pgcode", None)
		if pgcode == "42P01":
			return True
		if cur.__class__.__name__ == "UndefinedTable":
			return True
		cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
	return False


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/stocks/search")
	def api_stocks_search():
		query = (flask.request.args.get("q") or "").strip()
		limit = flask.request.args.get("limit", default=20, type=int)
		if limit <= 0:
			limit = 20
		limit = min(limit, 100)

		if not query:
			return flask.jsonify({"ok": True, "query": "", "count": 0, "results": []})
		alpaca_db, db_err = _get_alpaca_db_client(ctx)
		if db_err or alpaca_db is None:
			return flask.jsonify({
				"ok": True,
				"query": query,
				"count": 0,
				"results": [],
				"message": db_err or "Alpaca DB is not configured.",
			})

		conf = _load_alpaca_table_conf(ctx)
		equities_table = _quote_table(conf["ALPACA_EQUITIES_TABLE"])
		bars_table = _quote_table(conf["ALPACA_STOCK_BARS_TABLE"])
		symbol_col = _quote_ident(conf["ALPACA_EQUITIES_SYMBOL_COLUMN"])
		name_col = _quote_ident(conf["ALPACA_EQUITIES_NAME_COLUMN"])
		class_col = _quote_ident(conf["ALPACA_EQUITIES_CLASS_COLUMN"])
		exchange_col = _quote_ident(conf["ALPACA_EQUITIES_EXCHANGE_COLUMN"])
		status_col = _quote_ident(conf["ALPACA_EQUITIES_STATUS_COLUMN"])
		tradable_col = _quote_ident(conf["ALPACA_EQUITIES_TRADABLE_COLUMN"])
		bars_symbol_col = _quote_ident(conf["ALPACA_STOCK_BARS_SYMBOL_COLUMN"])
		bars_timeframe_col = _quote_ident(conf["ALPACA_STOCK_BARS_TIMEFRAME_COLUMN"])
		default_timeframe = (conf.get("ALPACA_STOCK_DEFAULT_TIMEFRAME") or "5Min").strip()

		sql_query = (
			f"SELECT e.{symbol_col} AS symbol, e.{name_col} AS name, e.{exchange_col} AS exchange, "
			f"e.{status_col} AS status, e.{tradable_col} AS tradable "
			f"FROM {equities_table} e "
			f"WHERE e.{class_col} = %s "
			f"AND EXISTS ("
			f"SELECT 1 FROM {bars_table} b "
			f"WHERE b.{bars_symbol_col} = e.{symbol_col} "
			f"AND b.{bars_timeframe_col} = %s "
			f"LIMIT 1"
			f") "
			f"AND (UPPER(e.{symbol_col}) LIKE UPPER(%s) || '%%' OR e.{name_col} ILIKE %s) "
			f"ORDER BY "
			f"CASE "
			f"WHEN UPPER(e.{symbol_col}) = UPPER(%s) THEN 0 "
			f"WHEN UPPER(e.{symbol_col}) LIKE UPPER(%s) || '%%' THEN 1 "
			f"WHEN e.{name_col} ILIKE %s THEN 2 "
			f"ELSE 3 "
			f"END, e.{symbol_col} ASC "
			f"LIMIT %s;"
		)
		query_like = f"%{query}%"
		try:
			rows = alpaca_db.execute_query(
				sql_query,
				("us_equity", default_timeframe, query, query_like, query, query, query_like, limit),
			) or []
		except Exception as exc:
			if _is_missing_relation_error(exc):
				return flask.jsonify({
					"ok": True,
					"query": query,
					"count": 0,
					"results": [],
					"message": (
						"Stock tables are not available in this database yet. "
						"Check src/config/alpaca_tables.conf and ensure the configured tables exist."
					),
				})
			raise
		return flask.jsonify({
			"ok": True,
			"query": query,
			"count": len(rows),
			"results": rows,
		})

	@api.route("/api/stocks/<symbol>/prices")
	def api_stock_prices(symbol: str):
		sym = (symbol or "").strip().upper()
		if not sym:
			return flask.jsonify({"ok": False, "message": "Symbol is required."}), 400
		alpaca_db, db_err = _get_alpaca_db_client(ctx)
		if db_err or alpaca_db is None:
			return flask.jsonify({
				"ok": False,
				"message": db_err or "Alpaca DB is not configured.",
				"symbol": sym,
				"count": 0,
				"points": [],
				"summary": {
					"latest_close": None,
					"prev_close": None,
					"change": None,
					"change_pct": None,
				},
			}), 503

		conf = _load_alpaca_table_conf(ctx)
		default_timeframe = (conf.get("ALPACA_STOCK_DEFAULT_TIMEFRAME") or "5Min").strip()
		timeframe = (flask.request.args.get("timeframe") or default_timeframe).strip()
		range_key = (flask.request.args.get("range") or "1W").strip().upper()
		limit = flask.request.args.get("limit", default=2000, type=int)
		if limit <= 0:
			limit = 2000
		limit = min(limit, 10000)

		start_param = flask.request.args.get("start")
		end_param = flask.request.args.get("end")
		now = datetime.now(timezone.utc)
		end_dt = _parse_iso_or_date(end_param) if end_param else now
		try:
			rolling_window_days = int((conf.get("ALPACA_STOCK_ROLLING_WINDOW_DAYS") or "").strip() or "7")
		except Exception:
			rolling_window_days = 7

		if start_param:
			start_dt = _parse_iso_or_date(start_param)
		else:
			if range_key == "1D":
				start_dt = _start_for_1d_window(end_dt)
			elif range_key in {"1W", "MAX"} and rolling_window_days > 0:
				start_dt = end_dt - timedelta(days=rolling_window_days)
			else:
				delta = _range_delta(range_key)
				start_dt = datetime(1970, 1, 1, tzinfo=timezone.utc) if delta is None else end_dt - delta

		if rolling_window_days > 0:
			rolling_cutoff = end_dt - timedelta(days=rolling_window_days)
			if start_dt < rolling_cutoff:
				start_dt = rolling_cutoff

		if start_dt > end_dt:
			return flask.jsonify({"ok": False, "message": "start must be <= end."}), 400
		bars_table = _quote_table(conf["ALPACA_STOCK_BARS_TABLE"])
		symbol_col = _quote_ident(conf["ALPACA_STOCK_BARS_SYMBOL_COLUMN"])
		ts_col = _quote_ident(conf["ALPACA_STOCK_BARS_TS_COLUMN"])
		timeframe_col = _quote_ident(conf["ALPACA_STOCK_BARS_TIMEFRAME_COLUMN"])
		open_col = _quote_ident(conf["ALPACA_STOCK_BARS_OPEN_COLUMN"])
		high_col = _quote_ident(conf["ALPACA_STOCK_BARS_HIGH_COLUMN"])
		low_col = _quote_ident(conf["ALPACA_STOCK_BARS_LOW_COLUMN"])
		close_col = _quote_ident(conf["ALPACA_STOCK_BARS_CLOSE_COLUMN"])
		volume_col = _quote_ident(conf["ALPACA_STOCK_BARS_VOLUME_COLUMN"])
		trade_count_col = _quote_ident(conf["ALPACA_STOCK_BARS_TRADE_COUNT_COLUMN"])
		vwap_col = _quote_ident(conf["ALPACA_STOCK_BARS_VWAP_COLUMN"])

		sql_query = (
			f"SELECT "
			f"{ts_col} AS ts, "
			f"{open_col} AS open, "
			f"{high_col} AS high, "
			f"{low_col} AS low, "
			f"{close_col} AS close, "
			f"{volume_col} AS volume, "
			f"{trade_count_col} AS trade_count, "
			f"{vwap_col} AS vwap "
			f"FROM {bars_table} "
			f"WHERE {symbol_col} = %s "
			f"AND {timeframe_col} = %s "
			f"AND {ts_col} >= %s "
			f"AND {ts_col} <= %s "
			f"ORDER BY {ts_col} ASC "
			f"LIMIT %s;"
		)
		try:
			rows = alpaca_db.execute_query(
				sql_query,
				(sym, timeframe, start_dt, end_dt, limit),
			) or []
		except Exception as exc:
			if _is_missing_relation_error(exc):
				return flask.jsonify({
					"ok": False,
					"message": (
						"Stock price tables are not available in this database yet. "
						"Check src/config/alpaca_tables.conf and ensure the configured tables exist."
					),
					"symbol": sym,
					"timeframe": timeframe,
					"range": range_key,
					"count": 0,
					"points": [],
					"summary": {
						"latest_close": None,
						"prev_close": None,
						"change": None,
						"change_pct": None,
					},
				}), 503
			raise

		points = [{
			"ts": _to_iso(r.get("ts")),
			"open": float(r["open"]) if r.get("open") is not None else None,
			"high": float(r["high"]) if r.get("high") is not None else None,
			"low": float(r["low"]) if r.get("low") is not None else None,
			"close": float(r["close"]) if r.get("close") is not None else None,
			"volume": int(r["volume"]) if r.get("volume") is not None else None,
			"trade_count": int(r["trade_count"]) if r.get("trade_count") is not None else None,
			"vwap": float(r["vwap"]) if r.get("vwap") is not None else None,
		} for r in rows]

		latest_close = points[-1]["close"] if points else None
		first_close = points[0]["close"] if points else None
		prev_close = points[-2]["close"] if len(points) > 1 else None

		# Period change: start of selected window -> latest point.
		change = None
		change_pct = None
		if latest_close is not None and first_close is not None:
			change = latest_close - first_close
			change_pct = (change / first_close) * 100 if first_close != 0 else None

		# Intrabar change: previous point -> latest point (returned for debugging/UI options).
		change_from_prev = None
		change_from_prev_pct = None
		if latest_close is not None and prev_close is not None:
			change_from_prev = latest_close - prev_close
			change_from_prev_pct = (change_from_prev / prev_close) * 100 if prev_close != 0 else None

		return flask.jsonify({
			"ok": True,
			"symbol": sym,
			"timeframe": timeframe,
			"range": range_key,
			"start": _to_iso(start_dt),
			"end": _to_iso(end_dt),
			"count": len(points),
			"points": points,
			"summary": {
				"first_close": first_close,
				"latest_close": latest_close,
				"prev_close": prev_close,
				"change": change,
				"change_pct": change_pct,
				"change_from_prev": change_from_prev,
				"change_from_prev_pct": change_from_prev_pct,
			},
		})
