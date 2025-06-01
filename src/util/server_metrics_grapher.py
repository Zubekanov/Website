import os
import time
from datetime import datetime
import psycopg2
import psycopg2.extras
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_conn = psycopg2.connect(
	dbname="personal_website_database",
	user="Zubekanov",
)

def _fetch_rows(sql_query, params=None):
	"""
	Execute a SELECT returning columns: ts, cpu_percent, ram_used, disk_used, cpu_temp.
	Returns a list of tuples: [(ts_int, cpu, ram, disk, temp), ...].
	"""
	with _conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
		cur.execute(sql_query, params or [])
		rows = cur.fetchall()
	# Convert each row (a dict-like) into a tuple
	return [
		(
			row["ts"],
			row["cpu_percent"],
			row["ram_used"],
			row["disk_used"],
			row["cpu_temp"]
		)
		for row in rows
	]


def _to_series(rows):
	"""
	Given a list of (ts, cpu, ram, disk, temp), return:
	  times = [datetime, ...]
	  metrics = {
	    "cpu_percent": [...],
	    "ram_used": [...],
	    "disk_used": [...],
	    "cpu_temp": [...]
	  }
	"""
	times = []
	metrics = {
		"cpu_percent": [],
		"ram_used": [],
		"disk_used": [],
		"cpu_temp": []
	}
	for ts, cpu, ram, disk, temp in rows:
		times.append(datetime.fromtimestamp(ts))
		metrics["cpu_percent"].append(cpu)
		metrics["ram_used"].append(ram)
		metrics["disk_used"].append(disk)
		metrics["cpu_temp"].append(temp)
	return times, metrics


def compute_rolling(times, values, window_seconds):
	"""
	Compute a time-based rolling average over a fixed window (window_seconds).
	Assumes `times` sorted ascending. Returns (roll_times, roll_vals).
	Each roll_time corresponds to original timestamp,
	and roll_val is the average of all values with timestamp in [ts - window_seconds, ts].
	"""
	ts_list = [t.timestamp() for t in times]
	roll_times = []
	roll_vals = []

	for i, ts_i in enumerate(ts_list):
		start_window = ts_i - window_seconds
		window_vals = [
			v for j, v in enumerate(values)
			if start_window <= ts_list[j] <= ts_i
		]
		if window_vals:
			roll_times.append(times[i])
			roll_vals.append(sum(window_vals) / len(window_vals))

	return roll_times, roll_vals


def plot_single_metric_raw(times, values, roll_times, roll_vals, filename, ylabel, title):
	"""
	Plot raw data points (broken on gaps) + rolling average. Used only for last hour.
	"""
	fig, ax = plt.subplots(figsize=(10, 4))
	now_ts = time.time()

	# Break raw data into contiguous segments on large gaps
	segments_x = []
	segments_y = []
	curr_x = [times[0]]
	curr_y = [values[0]]

	for i in range(1, len(times)):
		tn = times[i]
		tp = times[i - 1]
		diff = (tn - tp).total_seconds()
		# For last hour, expected interval is always 5 seconds
		if diff > 5 * 2:
			segments_x.append(curr_x)
			segments_y.append(curr_y)
			curr_x = [tn]
			curr_y = [values[i]]
		else:
			curr_x.append(tn)
			curr_y.append(values[i])

	segments_x.append(curr_x)
	segments_y.append(curr_y)

	# Plot each continuous segment
	for xs, ys in zip(segments_x, segments_y):
		ax.plot(xs, ys, linewidth=1)

	# Overlay rolling average if present
	if roll_times and roll_vals:
		ax.plot(roll_times, roll_vals, linestyle="--", label="Rolling Avg")
		ax.legend()

	ax.set_ylabel(ylabel)
	ax.set_title(title)

	# X-axis format: HH:MM
	fmt = mdates.DateFormatter("%H:%M")
	ax.xaxis.set_major_formatter(fmt)
	fig.autofmt_xdate(rotation=30, ha="right")

	plt.tight_layout()
	plt.savefig(filename)
	plt.close(fig)
	print(f"Saved plot: {filename}")


def plot_single_metric_aggregated(bucket_times, bucket_vals, filename, ylabel, title):
	"""
	Plot a single aggregated time series (no raw points).
	"""
	fig, ax = plt.subplots(figsize=(10, 4))
	ax.plot(bucket_times, bucket_vals, linewidth=1)

	ax.set_ylabel(ylabel)
	ax.set_title(title)

	# Choose x-axis formatting based on total span
	min_time = bucket_times[0]
	max_time = bucket_times[-1]
	span_days = (max_time - min_time).days

	if span_days < 1:
		fmt = mdates.DateFormatter("%H:%M")
	elif span_days <= 365:
		fmt = mdates.DateFormatter("%b %d")
	else:
		fmt = mdates.DateFormatter("%Y %b %d")

	ax.xaxis.set_major_formatter(fmt)
	fig.autofmt_xdate(rotation=30, ha="right")

	plt.tight_layout()
	plt.savefig(filename)
	plt.close(fig)
	print(f"Saved plot: {filename}")


def plot_last_hour():
	"""
	Fetch raw 5-second rows for the last hour, plot each metric with a 1-minute rolling average.
	"""
	one_hour_ago = int(time.time()) - 3600
	query = """
		SELECT ts, cpu_percent, ram_used, disk_used, cpu_temp
		  FROM server_metrics
		 WHERE ts >= %s
		 ORDER BY ts;
	"""
	rows = _fetch_rows(query, [one_hour_ago])
	if not rows:
		print("No data in the last hour.")
		return

	times, metrics = _to_series(rows)

	# Compute 1-minute rolling (60 seconds)
	cpu_roll_t, cpu_roll_v = compute_rolling(times, metrics["cpu_percent"], 60)
	temp_roll_t, temp_roll_v = compute_rolling(times, metrics["cpu_temp"], 60)

	for key, ylabel, roll_t, roll_v in [
		("cpu_percent", "CPU %", cpu_roll_t, cpu_roll_v),
		("ram_used", "RAM Used (GiB)", [], []),
		("disk_used", "Disk Used (GiB)", [], []),
		("cpu_temp", "CPU Temp (°C)", temp_roll_t, temp_roll_v),
	]:
		filename = os.path.join("/home/Zubekanov/Repositories/Website_Prod/plots", f"last_hour_{key}.png")
		title = f"{ylabel} (Last Hour)"
		plot_single_metric_raw(times, metrics[key], roll_t, roll_v, filename, ylabel, title)


def plot_last_day():
	"""
	Fetch raw 5-second rows for the last day, bucket into 1-minute intervals,
	then plot only the minute-level series (no raw points). CPU/temp get a 12-minute rolling.
	"""
	one_day_ago = int(time.time()) - 86400
	query = """
		SELECT ts, cpu_percent, ram_used, disk_used, cpu_temp
		  FROM server_metrics
		 WHERE ts >= %s
		 ORDER BY ts;
	"""
	rows = _fetch_rows(query, [one_day_ago])
	if not rows:
		print("No data in the last day.")
		return

	# Bucket by minute: key = floor(ts / 60) * 60
	buckets = {}
	for ts, cpu, ram, disk, temp in rows:
		min_ts = ts - (ts % 60)
		buckets.setdefault(min_ts, []).append((cpu, ram, disk, temp))

	sorted_minutes = sorted(buckets.keys())
	minute_times = []
	agg = {
		"cpu_percent": [],
		"ram_used": [],
		"disk_used": [],
		"cpu_temp": []
	}

	for m_ts in sorted_minutes:
		minute_times.append(datetime.fromtimestamp(m_ts))
		samples = buckets[m_ts]
		cols = list(zip(*samples))
		agg["cpu_percent"].append(sum(cols[0]) / len(cols[0]))
		agg["ram_used"].append(sum(cols[1]) / len(cols[1]))
		agg["disk_used"].append(sum(cols[2]) / len(cols[2]))
		agg["cpu_temp"].append(sum(cols[3]) / len(cols[3]) if cols[3] else None)

	# 12-minute rolling window = 12 * 60 = 720 seconds
	cpu_roll_t, cpu_roll_v = compute_rolling(minute_times, agg["cpu_percent"], 720)
	temp_roll_t, temp_roll_v = compute_rolling(minute_times, agg["cpu_temp"], 720)

	for key, ylabel, roll_t, roll_v in [
		("cpu_percent", "CPU %", cpu_roll_t, cpu_roll_v),
		("ram_used", "RAM Used (GiB)", [], []),
		("disk_used", "Disk Used (GiB)", [], []),
		("cpu_temp", "CPU Temp (°C)", temp_roll_t, temp_roll_v),
	]:
		filename = os.path.join("/home/Zubekanov/Repositories/Website_Prod/plots", f"last_day_{key}.png")
		title = f"{ylabel} (Last Day)"
		if roll_t and roll_v:
			plot_single_metric_aggregated(roll_t, roll_v, filename, ylabel, title)
		else:
			plot_single_metric_aggregated(minute_times, agg[key], filename, ylabel, title)


def plot_all_time():
	"""
	Fetch all raw rows, bucket into 1-day intervals,
	then plot only the daily series (no raw points). CPU/temp get a 12-day rolling.
	"""
	query = """
		SELECT ts, cpu_percent, ram_used, disk_used, cpu_temp
		  FROM server_metrics
		 ORDER BY ts;
	"""
	rows = _fetch_rows(query)
	if not rows:
		print("No data at all.")
		return

	# Bucket by day: key = floor(ts / 86400) * 86400
	buckets = {}
	for ts, cpu, ram, disk, temp in rows:
		day_ts = ts - (ts % 86400)
		buckets.setdefault(day_ts, []).append((cpu, ram, disk, temp))

	sorted_days = sorted(buckets.keys())
	day_times = []
	agg = {
		"cpu_percent": [],
		"ram_used": [],
		"disk_used": [],
		"cpu_temp": []
	}

	for d_ts in sorted_days:
		day_times.append(datetime.fromtimestamp(d_ts))
		samples = buckets[d_ts]
		cols = list(zip(*samples))
		agg["cpu_percent"].append(sum(cols[0]) / len(cols[0]))
		agg["ram_used"].append(sum(cols[1]) / len(cols[1]))
		agg["disk_used"].append(sum(cols[2]) / len(cols[2]))
		agg["cpu_temp"].append(sum(cols[3]) / len(cols[3]) if cols[3] else None)

	# 12-day rolling window = 12 * 86400 = 1,036,800 seconds
	cpu_roll_t, cpu_roll_v = compute_rolling(day_times, agg["cpu_percent"], 12 * 86400)
	temp_roll_t, temp_roll_v = compute_rolling(day_times, agg["cpu_temp"], 12 * 86400)

	for key, ylabel, roll_t, roll_v in [
		("cpu_percent", "CPU %", cpu_roll_t, cpu_roll_v),
		("ram_used", "RAM Used (GiB)", [], []),
		("disk_used", "Disk Used (GiB)", [], []),
		("cpu_temp", "CPU Temp (°C)", temp_roll_t, temp_roll_v),
	]:
		filename = os.path.join("/home/Zubekanov/Repositories/Website_Prod/plots", f"all_time_{key}.png")
		title = f"{ylabel} (All Time)"
		if roll_t and roll_v:
			plot_single_metric_aggregated(roll_t, roll_v, filename, ylabel, title)
		else:
			plot_single_metric_aggregated(day_times, agg[key], filename, ylabel, title)


def plot_ranges():
	"""
	Generate all twelve plots: last hour, last day, all time × four metrics each.
	"""
	plot_last_hour()
	plot_last_day()
	plot_all_time()


if __name__ == "__main__":
	plot_ranges()
