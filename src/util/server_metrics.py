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

def get_range_metrics(start: int, stop: int, step: int) -> dict:
	"""
	Fetch metrics between `start` and `stop` (inclusive),
	sampled every `step` seconds (rounded up to the nearest 5).
	Returns a dict of series just like get_last_hour_metrics()/get_all_metrics().
	"""
	# Round step up to nearest multiple of 5
	step = ((step + 4) // 5) * 5
	if step <= 0:
		step = 5

	if not start or start < 0:
		start = int(time.time()) - 3600
	if not stop or stop < 0:
		stop = int(time.time())
	if start > stop:
		raise ValueError("Start timestamp must be less than or equal to stop timestamp.")

	query = """
		SELECT
			ts,
			cpu_percent,
			ram_used,
			disk_used,
			cpu_temp
		FROM server_metrics
		WHERE ts >= %s
		  AND ts <= %s
		ORDER BY ts;
	"""
	client = PSQLClient()
	rows = client.execute(query, [start, stop])
 
	step_items = step / 5
	# If there is an even number of items, averaging over item - 1 ensures full coverage.
	# If there is an odd number, that number of items will overlap on an intermediate value.
	# But it saves a lot of work to just accept the overlap, and it barely affects the result.
	if step_items % 2 == 0:
		step_items -= 1
  
	# Convert rows into a dict of form
	# { ts : { "cpu_percent": ..., "ram_used": ..., "disk_used": ..., "cpu_temp": ... } }
	rows = {r["ts"]: r for r in rows}
	metrics = {k: [] for k in ("cpu_percent", "ram_used", "disk_used", "cpu_temp")}
 
	for step_ts in range(start, stop + 1, step):
		# From above conversion, step_items is always odd.
		half_step_items = (step_items - 1) / 2
		step_lower = step_ts - half_step_items * 5
		step_upper = step_ts + half_step_items * 5
  
		valid_values = 0
		step_row = {
			"ts": step_ts,
			"cpu_percent": 0,
			"ram_used": 0,
			"disk_used": 0,
			"cpu_temp": 0
		}
  
		for inter_step_ts in range(step_lower, step_upper + 1, 5):
			if inter_step_ts in rows.keys():
				valid_values += 1
				step_row["cpu_percent"] += rows[inter_step_ts]["cpu_percent"]
				step_row["ram_used"] += rows[inter_step_ts]["ram_used"]
				step_row["disk_used"] += rows[inter_step_ts]["disk_used"]
				step_row["cpu_temp"] += rows[inter_step_ts]["cpu_temp"]
		
		if valid_values> 0:
			metrics["cpu_percent"].append({ "x": step_ts, "y": step_row["cpu_percent"] / valid_values })
			metrics["ram_used"].append({ "x": step_ts, "y": step_row["ram_used"] / valid_values })
			metrics["disk_used"].append({ "x": step_ts, "y": step_row["disk_used"] / valid_values })
			metrics["cpu_temp"].append({ "x": step_ts, "y": step_row["cpu_temp"] / valid_values })

	return metrics

