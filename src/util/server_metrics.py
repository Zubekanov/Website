import os
import time
import threading

import psutil

from datetime import datetime
from util.config_reader import ConfigReader
from util.psql_manager import PSQLClient

_SLEEP_INTERVAL = 5
_initialisation_time = time.time()
_worker_started = False
_last_fetched_metrics = {}

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

def _static_info_path():
	base = ConfigReader.logs_dir()
	return os.path.join(base, "server_metrics", "static_info.log")

def get_static_metrics():
	return {
		"ram_total": get_ram_total(),
		"disk_total": get_disk_total()
	}

def log_server_metrics():
	"""
	Fetch current metrics and insert into `server_metrics` table.
	Also update _last_fetched_metrics so get_latest_metrics() still works.
	"""
	global _last_fetched_metrics
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
	_last_fetched_metrics = data

def server_metrics_worker():
	"""
	Periodically calls log_server_metrics() every _SLEEP_INTERVAL seconds.
	Static‐info (RAM/DISK) is still appended to flat file if it changes.
	"""
	print("Server metrics worker started.")

	# Ensure the directory for static_info exists (same as before)
	metrics_dir = ConfigReader.logs_dir()
	static_dir = os.path.join(metrics_dir, "server_metrics")
	os.makedirs(static_dir, exist_ok=True)
	static_log_path = os.path.join(static_dir, "static_info.log")

	def _read_last_static():
		if not os.path.exists(static_log_path):
			return None, None
		with open(static_log_path, "r") as f:
			lines = [l.strip() for l in f if l.strip()]
		if not lines:
			return None, None
		_, ram_s, disk_s = lines[-1].split(",")
		return float(ram_s), float(disk_s)

	# Record static metrics if they changed (same as original)
	last_ram, last_disk = _read_last_static()
	curr_ram = get_ram_total()
	curr_disk = get_disk_total()
	if curr_ram != last_ram or curr_disk != last_disk:
		with open(static_log_path, "a") as f:
			f.write(f"{int(time.time())},{curr_ram},{curr_disk}\n")
		print(f"[static_info] appended new specs: RAM={curr_ram} GiB, Disk={curr_disk} GiB")

	last_log = 0
	while True:
		now = int(time.time())
		if now % _SLEEP_INTERVAL == 0 and last_log != now:
			log_server_metrics()
			last_log = now
		time.sleep(0.5)

def start_server_metrics_thread():
	global _worker_started
	if _worker_started:
		return
	_worker_started = True
	threading.Thread(target=server_metrics_worker, daemon=True).start()

def get_latest_metrics():
	"""
	Return the most recent sample that was written (or {} if none).
	"""
	return _last_fetched_metrics

def get_last_hour_metrics():
	"""
	Fetch all rows from server_metrics where ts >= (now - 3600).
	Return a dict in the same shape as previously:
	{ "cpu_percent": [ {x: ts, y: val}, … ], … }
	"""
	cutoff = int(time.time()) - 3600
	query = """
		SELECT ts, cpu_percent, ram_used, disk_used, cpu_temp
		  FROM server_metrics
		 WHERE ts >= %s
		 ORDER BY ts;
	"""
	client = PSQLClient()
	rows = client.execute(query, [cutoff])

	# Build the same output structure
	metrics = {k: [] for k in ("cpu_percent", "ram_used", "disk_used", "cpu_temp")}
	for r in rows:
		ts = r["ts"]
		metrics["cpu_percent"].append({ "x": ts, "y": r["cpu_percent"] })
		metrics["ram_used"].append({ "x": ts, "y": r["ram_used"] })
		metrics["disk_used"].append({ "x": ts, "y": r["disk_used"] })
		metrics["cpu_temp"].append({ "x": ts, "y": r["cpu_temp"] })
	return metrics

def get_all_metrics():
	"""
	Fetch all rows from server_metrics (no compression). Return same shape:
	{ "cpu_percent": [ {x: ts, y: val}, … ], … }
	If you ever want to add database‐side aggregation, do it here.
	"""
	query = """
		SELECT ts, cpu_percent, ram_used, disk_used, cpu_temp
		  FROM server_metrics
		 ORDER BY ts;
	"""
	client = PSQLClient()
	rows = client.execute(query)

	metrics = {k: [] for k in ("cpu_percent", "ram_used", "disk_used", "cpu_temp")}
	for r in rows:
		ts = r["ts"]
		metrics["cpu_percent"].append({ "x": ts, "y": r["cpu_percent"] })
		metrics["ram_used"].append({ "x": ts, "y": r["ram_used"] })
		metrics["disk_used"].append({ "x": ts, "y": r["disk_used"] })
		metrics["cpu_temp"].append({ "x": ts, "y": r["cpu_temp"] })
	return metrics
