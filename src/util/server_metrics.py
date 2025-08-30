import os
import time
import threading

import psutil
from pathlib import Path

from datetime import datetime
from util.config_reader import ConfigReader
from util.psql_manager import PSQLClient
from util.lock_manager import get_lock, get_lock_file_path
import logging

_LOCK_NAME = "server_metrics_lock"
_LOCK = None

_SLEEP_INTERVAL = 5
_initialisation_time = time.time()
_worker_started = False

def get_local_data():
	return {
		"cpu_percent": psutil.cpu_percent(interval=None),
		"ram_used":    round(psutil.virtual_memory().used / 1073741824, 2),
		"disk_used":   round(psutil.disk_usage("/").used / 1073741824, 1),
		"cpu_temp":    _get_cpu_temp(),
	}

def _get_cpu_temp():
	try:
		temps = psutil.sensors_temperatures()
		for key in ("cpu-thermal", "coretemp"):
			if key in temps:
				return round(temps[key][0].current, 2)
		with open("/sys/class/thermal/thermal_zone0/temp") as f:
			return round(int(f.read()) / 1000.0, 2)
	except Exception:
		return None

def get_ram_total():
	return round(psutil.virtual_memory().total / 1073741824, 2)

def get_disk_total():
	return round(psutil.disk_usage("/").total / 1073741824, 2)

def get_uptime():
	return int(time.time() - _initialisation_time)

def get_static_metrics():
	return {
		"ram_total": get_ram_total(),
		"disk_total": get_disk_total()
	}

def log_server_metrics():
	"""
	Fetch current metrics and insert into `server_metrics` table.
	"""
	data = get_local_data()
	ts = int(time.time())

	# Build the row dict matching table columns
	row = {
		"ts":          ts,
		"cpu_percent": data["cpu_percent"],
		"ram_used":    data["ram_used"],
		"disk_used":   data["disk_used"],
		"cpu_temp":    data["cpu_temp"]
	}

	# Insert or ignore if ts collision
	client = PSQLClient()
	try:
		client.insert_row("server_metrics", row)
	except Exception:
		# In case of conflict or any error, ignore or log as needed
		pass

	# Keep the same structure for get_latest_metrics()
	data["timestamp"] = ts

def server_metrics_worker():
	"""
	Periodically calls log_server_metrics() every _SLEEP_INTERVAL seconds.
	Static-info (RAM/DISK) is still appended to a flat file if it changes.
	"""
	logging.debug("Server metrics worker started.")

	# Use namespace path (no call) → Path object
	metrics_dir: Path = ConfigReader().logs_dir.base
	static_dir: Path = metrics_dir / "server_metrics"
	static_dir.mkdir(parents=True, exist_ok=True)
	static_log_path: Path = static_dir / "static_info.log"

	def _read_last_static():
		if not static_log_path.exists():
			return None, None
		with static_log_path.open("r") as f:
			lines = [l.strip() for l in f if l.strip()]
		if not lines:
			return None, None
		_, ram_s, disk_s = lines[-1].split(",")
		return float(ram_s), float(disk_s)

	# Record static metrics if they changed
	last_ram, last_disk = _read_last_static()
	curr_ram = get_ram_total()
	curr_disk = get_disk_total()
	if curr_ram != last_ram or curr_disk != last_disk:
		with static_log_path.open("a") as f:
			f.write(f"{int(time.time())},{curr_ram},{curr_disk}\n")
		logging.debug(f"[static_info] appended new specs: RAM={curr_ram} GiB, Disk={curr_disk} GiB")

	last_log = 0
	while True:
		now = int(time.time())
		if now % _SLEEP_INTERVAL == 0 and last_log != now:
			log_server_metrics()
			last_log = now
		time.sleep(0.5)

def start_server_metrics_thread():
	global _LOCK
	_LOCK = open(get_lock_file_path(_LOCK_NAME), "w")
	res = get_lock(_LOCK_NAME, _LOCK)
	if not res:
		return
	logging.info(f"Server metrics thread started with lock '{_LOCK}'")
	threading.Thread(target=server_metrics_worker, daemon=True).start()

def get_latest_metrics():
	"""
	Return the most recent sample that was written (or {} if none).
	"""
	client = PSQLClient()
	rows = client.get_rows_by_predicates(
		table="server_metrics",
		predicates=[],
		columns=["ts", "cpu_percent", "ram_used", "disk_used", "cpu_temp"],
		order_by=[("ts", "DESC")],
		limit=1
	)
	if not rows:
		return {}
	row = rows[0]
	return {
		"timestamp": row["ts"],
		"cpu_percent": row["cpu_percent"],
		"ram_used": row["ram_used"],
		"disk_used": row["disk_used"],
		"cpu_temp": row["cpu_temp"]
	}


def get_range_metrics(start: int, stop: int, step: int) -> dict:
	"""
	Fetch metrics between `start` and `stop` (inclusive),
	sampled every `step` seconds (rounded up to the nearest 5).
	Clamps out-of-bounds start/stop to the DB range so you never iterate
	over an empty span.
	Returns a dict of series just like get_last_hour_metrics()/get_all_metrics().
	"""
	step = int(step) if step is not None else 5
	step = max(5, ((step + 4) // 5) * 5)

	client = PSQLClient()
	bounds = client.get_min_max("server_metrics", "ts")
	min_ts = bounds["min_val"]
	max_ts = int(time.time())

	if start is None:
		start = int(time.time()) - 3600
	if start < min_ts:
		start = min_ts
	if stop is None or stop > max_ts:
		stop = max_ts
	if start > stop:
		raise ValueError("Start timestamp must be ≤ stop timestamp.")

	start += (5 - start % 5) % 5    # bump up to next multiple of 5
	stop  -= stop % 5               # drop down to previous multiple of 5

	rows = client.get_rows_by_predicates(
		table="server_metrics",
		predicates=[
			("ts", ">=", start),
			("ts", "<=", stop),
		],
		columns=["ts", "cpu_percent", "ram_used", "disk_used", "cpu_temp"],
		order_by=[("ts", "ASC")]
	)

	row_map = {r["ts"]: r for r in rows}

	n_per_step = step // 5
	if n_per_step % 2 == 0:
		n_per_step -= 1
	half_window = ((n_per_step - 1) // 2) * 5  # in seconds

	metrics = {k: [] for k in ("cpu_percent", "ram_used", "disk_used", "cpu_temp")}

	for ts_center in range(start, stop + 1, step):
		sums = {"cpu_percent": 0, "ram_used": 0, "disk_used": 0, "cpu_temp": 0}
		count = 0
		low  = ts_center - half_window
		high = ts_center + half_window

		for t in range(low, high + 1, 5):
			if t in row_map:
				count += 1
				row = row_map[t]
				sums["cpu_percent"] += row["cpu_percent"]
				sums["ram_used"]    += row["ram_used"]
				sums["disk_used"]   += row["disk_used"]
				sums["cpu_temp"]    += row["cpu_temp"]

		if count:
			metrics["cpu_percent"].append({ "x": ts_center, "y": sums["cpu_percent"] / count })
			metrics["ram_used"].append({    "x": ts_center, "y": sums["ram_used"]    / count })
			metrics["disk_used"].append({   "x": ts_center, "y": sums["disk_used"]   / count })
			metrics["cpu_temp"].append({    "x": ts_center, "y": sums["cpu_temp"]    / count })

	return metrics