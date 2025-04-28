import os
import time
import psutil
import threading
import time
from util.configreader import ConfigReader

_server_metrics_thread_started = False

psutil.cpu_percent(interval=None)
compression_intervals = (
	60, 3600, 86400, 2592000
	# 1 minute, 1 hour, 1 day, 30 days
)
sleep_interval = 5
initialisation_time = time.time()

def log_server_metrics():
	log_dir = ConfigReader.get_logs_dir()
	log_server_metrics_dir = os.path.join(log_dir, "server_metrics")
	if not os.path.exists(log_server_metrics_dir):
		os.makedirs(log_server_metrics_dir)

	live_data = get_local_data()
	current_time = int(time.time())

	# Save live 5s interval metrics
	live_log_file_path = os.path.join(log_server_metrics_dir, "live_metrics.log")
	with open(live_log_file_path, "a") as f:
		values = [
			live_data["cpu_percent"],
			live_data["ram_used"],
			live_data["disk_used"],
			live_data["cpu_temp"]
		]
		f.write(f"{current_time}," + ",".join(map(str, values)) + "\n")
	
	return current_time

def compress_metrics_file():
	log_dir = ConfigReader.get_logs_dir()
	log_server_metrics_dir = os.path.join(log_dir, "server_metrics")
	live_log_path = os.path.join(log_server_metrics_dir, "live_metrics.log")
	compressed_log_path = os.path.join(log_server_metrics_dir, "compressed_metrics.log")

	now = time.time()

	lines = []
	with open(live_log_path, "r") as f:
		for line in f:
			epoch_str, *values = line.strip().split(",")
			lines.append((int(float(epoch_str)), list(map(float, values))))

	new_live_lines = []
	to_compress = {"minute": [], "hour": [], "day": []}

	for epoch, values in lines:
		age = now - epoch
		if age > compression_intervals[1] and age <= compression_intervals[2]:
			to_compress["minute"].append((epoch, values))
		elif age > compression_intervals[2] and age <= compression_intervals[3]:
			to_compress["hour"].append((epoch, values))
		elif age > compression_intervals[3]:
			to_compress["day"].append((epoch, values))
		else:
			new_live_lines.append((epoch, values))

	with open(compressed_log_path, "a") as f:
		for scale, entries in to_compress.items():
			if not entries:
				continue

			buckets = {}
			for epoch, values in entries:
				if scale == "minute":
					key = epoch // compression_intervals[0]
				elif scale == "hour":
					key = epoch // compression_intervals[1]
				else:
					key = epoch // compression_intervals[2]

				if key not in buckets:
					buckets[key] = []
				buckets[key].append(values)

			for key, vals_list in buckets.items():
				avg_vals = [sum(col) / len(col) for col in zip(*vals_list)]
				if scale == "minute":
					bucket_epoch = key * compression_intervals[0]
				elif scale == "hour":
					bucket_epoch = key * compression_intervals[1]
				else:
					bucket_epoch = key * compression_intervals[2]

				f.write(f"{bucket_epoch}," + ",".join(f"{v:.3f}" for v in avg_vals) + "\n")

	with open(live_log_path, "w") as f:
		for epoch, values in new_live_lines:
			f.write(f"{epoch}," + ",".join(f"{v:.3f}" for v in values) + "\n")

def server_metrics_worker():
	print("Server metrics worker started.")

	log_dir = ConfigReader.get_logs_dir()
	log_server_metrics_dir = os.path.join(log_dir, "server_metrics")
	if not os.path.exists(log_server_metrics_dir):
		os.makedirs(log_server_metrics_dir)

	static_data = {
		"ram_total": get_ram_total(),
		"disk_total": get_disk_total()
	}

	static_log_file_path = os.path.join(log_server_metrics_dir, "static_info.log")
	with open(static_log_file_path, "w") as f:
		# Save timestamped static values at boot
		current_time = int(time.time())
		f.write(f"{current_time},{static_data['ram_total']},{static_data['disk_total']}\n")

	last_compression = 0
	last_recorded_time = None
	while True:
		current_time = int(time.time())

		if current_time % 5 == 0:
			if last_recorded_time != current_time:
				last_recorded_time = current_time
				try:
					log_server_metrics()
					if time.time() - last_compression > compression_intervals[1]:
						compress_metrics_file()
						last_compression = time.time()
				except Exception as e:
					print(f"Error in server metrics worker: {e}")

		time.sleep(0.5)

import threading

_server_metrics_thread_started = False

def start_server_metrics_thread():
	global _server_metrics_thread_started

	if _server_metrics_thread_started:
		print("Server metrics worker already running. Skipping start.")
		return None
	
	_server_metrics_thread_started = True

	worker_thread = threading.Thread(target=server_metrics_worker, daemon=True)
	worker_thread.start()
	return worker_thread

def get_local_data():
	cpu_temp = get_cpu_temp()
	return {
		"cpu_percent": psutil.cpu_percent(interval=None),
		"ram_used" : round((psutil.virtual_memory().used / 1073741824), 2),
		"disk_used": round((psutil.disk_usage('/').used / 1073741824), 1),
		"cpu_temp": round(cpu_temp, 2) if cpu_temp is not None else None
	}

def get_ram_total():
	return round((psutil.virtual_memory().total / 1073741824), 2)

def get_disk_total():
	return round((psutil.disk_usage('/').total / 1073741824), 2)

def get_uptime():
	return int(time.time() - initialisation_time)

def get_cpu_temp():
	try:
		temps = psutil.sensors_temperatures()
		if "cpu-thermal" in temps:
			return temps["cpu-thermal"][0].current
		elif "coretemp" in temps:
			return temps["coretemp"][0].current
		else:
			# Fallback: manually read from system
			with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
				return int(f.read()) / 1000
	except Exception:
		return None
	
if __name__ == "__main__":
	data = get_local_data()
	print(data)
