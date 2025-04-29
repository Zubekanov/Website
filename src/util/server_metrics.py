import os
import time
import threading

import psutil

from datetime import datetime
from util.configreader import ConfigReader

_SLEEP_INTERVAL = 5
_COMPRESSION_INTERVALS = (
	60,       # 1 minute
	3600,     # 1 hour
	86400,    # 1 day
	2592000,  # 30 days
)
_initialisation_time = time.time()
_worker_started = False
_last_fetched_metrics = {}

def get_local_data():
	return {
		"cpu_percent": psutil.cpu_percent(interval=None),
		"ram_used": round(psutil.virtual_memory().used / 1073741824, 2),
		"disk_used": round(psutil.disk_usage("/").used / 1073741824, 1),
		"cpu_temp": _get_cpu_temp(),
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

def _ensure_metrics_dir():
	base = ConfigReader.get_logs_dir()
	path = os.path.join(base, "server_metrics")
	os.makedirs(path, exist_ok=True)
	return path

def _live_log_path():
	return os.path.join(_ensure_metrics_dir(), "live_metrics.log")

def _compressed_log_path():
	return os.path.join(_ensure_metrics_dir(), "compressed_metrics.log")

def _static_info_path():
	return os.path.join(_ensure_metrics_dir(), "static_info.log")

def get_static_metrics():
	return {
		"ram_total": get_ram_total(),
		"disk_total": get_disk_total()
	}

def log_server_metrics():
	global _last_fetched_metrics
	path = _live_log_path()
	data = get_local_data()
	ts = int(time.time())
	values = [data[k] for k in ("cpu_percent", "ram_used", "disk_used", "cpu_temp")]
	with open(path, "a") as f:
		f.write(f"{ts}," + ",".join(map(str, values)) + "\n")

	data["timestamp"] = ts
	_last_fetched_metrics = data

def compress_metrics_file():
	log_dir = ConfigReader.get_logs_dir()
	base = os.path.join(log_dir, "server_metrics")
	live_path = os.path.join(base, "live_metrics.log")
	comp_path = os.path.join(base, "compressed_metrics.log")

	print("Running server metrics compression…")
	start = time.time()

	all_entries = []

	if os.path.exists(live_path):
		with open(live_path, "r") as f:
			for line in f:
				ts_str, *vals = line.strip().split(",")
				all_entries.append((int(ts_str), list(map(float, vals))))

	old_live = len(all_entries)
				
	if os.path.exists(comp_path):
		with open(comp_path, "r") as f:
			for line in f:
				ts_str, *vals = line.strip().split(",")
				all_entries.append((int(ts_str), list(map(float, vals))))

	old_total = len(all_entries)
	old_comp = old_total - old_live
	all_entries.sort(key=lambda e: e[0])
	
	buckets = {
		"minute": {},
		"hour":   {},
		"day":    {}
	}
	minute_i, hour_i, day_i, month_i = _COMPRESSION_INTERVALS[:]

	new_live = []
	now = time.time()

	for ts, vals in all_entries:
		age = now - ts

		if age > hour_i and age <= day_i:
			interval = minute_i
			scale = "minute"
		elif age > day_i and age <= month_i:
			interval = hour_i
			scale = "hour"
		elif age > month_i:
			interval = day_i
			scale = "day"
		else:
			new_live.append((ts, vals))
			continue

		# Align the bucket to exact boundary: e.g. ts - (ts % 60)
		bucket_ts = ts - (ts % interval)
		buckets[scale].setdefault(bucket_ts, []).append(vals)

	new_comp = []
	for scale, mapping in buckets.items():
		for bucket_ts, list_of_vals in mapping.items():
			# compute column-wise mean
			cols = zip(*list_of_vals)
			avg = [sum(col) / len(col) for col in cols]
			new_comp.append((bucket_ts, avg))

	with open(comp_path, "w") as f:
		for ts, vals in sorted(new_comp, key=lambda x: x[0]):
			f.write(f"{ts}," + ",".join(f"{v:.2f}" for v in vals) + "\n")

	with open(live_path, "w") as f:
		for ts, vals in sorted(new_live, key=lambda x: x[0]):
			f.write(f"{ts}," + ",".join(f"{v:.2f}" for v in vals) + "\n")

	kept = len(new_live) + len(new_comp)
	reduction = int((1 - kept / old_total) * 100) if old_total else 0
	print(f"{old_live} live + {old_comp} compressed ({old_total} total) → {len(new_live)} live + {len(new_comp)} compressed ({kept} total) ({reduction}% reduction) in {time.time() - start:.2f}s")

def _get_earliest_live_ts(path: str) -> float:
	if not os.path.exists(path):
		return time.time()
	with open(path, "r") as f:
		for line in f:
			ts = line.split(",", 1)[0]
			try:
				return float(ts)
			except ValueError:
				continue
	return time.time()

def _format_schedule(ts: float) -> str:
	return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")

def server_metrics_worker():
	print("Server metrics worker started.")
	metrics_dir = _ensure_metrics_dir()
	static_log_path = os.path.join(metrics_dir, "static_info.log")

	def _read_last_static():
		if not os.path.exists(static_log_path):
			return None, None
		with open(static_log_path, "r") as f:
			lines = [l.strip() for l in f if l.strip()]
		if not lines:
			return None, None
		_, ram_s, disk_s = lines[-1].split(",")
		return float(ram_s), float(disk_s)

	last_ram, last_disk = _read_last_static()

	curr_ram = get_ram_total()
	curr_disk = get_disk_total()

	# Only append if either changed
	if curr_ram != last_ram or curr_disk != last_disk:
		with open(static_log_path, "a") as f:
			f.write(f"{int(time.time())},{curr_ram},{curr_disk}\n")
		print(f"[static_info] appended new specs: RAM={curr_ram} GiB, Disk={curr_disk} GiB")

	compress_metrics_file()

	# Calculate next compression time.
	# The time is always 1 hour later unless the log contains less than 1 hour of data.
	earliest = _get_earliest_live_ts(_live_log_path())
	next_compress = earliest + 7200
	now = time.time()
	delay = max(0, next_compress - now)
	print(
		f"Next compression scheduled for "
		f"{_format_schedule(next_compress)} "
		f"({int(delay/60)} min from now)"
	)

	last_log = 0
	while True:
		if int(time.time()) % _SLEEP_INTERVAL == 0 and last_log != int(time.time()):
			log_server_metrics()
			last_log = int(time.time())

			if time.time() >= next_compress:
				compress_metrics_file()
				# Recompute earliest in the freshly‐written live log
				earliest = _get_earliest_live_ts(_live_log_path())
				next_compress = earliest + 7200
				now = time.time()
				delay = max(0, next_compress - now)
				print(
					f"Next compression scheduled for "
					f"{_format_schedule(next_compress)} "
					f"({int(delay/60)} min from now)"
				)

		time.sleep(0.5)

def start_server_metrics_thread():
	global _worker_started
	if _worker_started:
		return
	_worker_started = True
	threading.Thread(target=server_metrics_worker, daemon=True).start()

def load_entries(path, since_ts=None):
	if not os.path.exists(path):
		return []

	with open(path) as f:
		parsed = []
		for line in f:
			parts = line.strip().split(",")
			if len(parts) < 5:
				continue
			ts = int(parts[0])
			vals = list(map(float, parts[1:5]))
			if since_ts is None or ts >= since_ts:
				parsed.append((ts, vals))
	return parsed

def get_latest_metrics():
	return _last_fetched_metrics

def get_last_hour_metrics():
	one_hour_ago = int(time.time()) - 3600
	entries = load_entries(_live_log_path(), since_ts=one_hour_ago)

	metrics = {k: [] for k in ("cpu_percent", "ram_used", "disk_used", "cpu_temp")}
	for ts, vals in entries:
		for name, val in zip(metrics, vals):
			metrics[name].append({"x": ts, "y": val})

	return metrics

def get_compressed_metrics():
	entries = load_entries(_compressed_log_path())
	metrics = {k: [] for k in ("cpu_percent", "ram_used", "disk_used", "cpu_temp")}
	for ts, vals in entries:
		for name, val in zip(metrics, vals):
			metrics[name].append({"x": ts, "y": val})

	return metrics
