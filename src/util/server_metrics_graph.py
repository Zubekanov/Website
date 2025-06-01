import os
import time
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Paths to metrics files (adjust if needed)
LOG_DIR = os.path.join(os.getcwd(), "src", "logs", "server_metrics")
LIVE_LOG_PATH = os.path.join(LOG_DIR, "live_metrics.log")
COMPRESSED_LOG_PATH = os.path.join(LOG_DIR, "compressed_metrics.log")

# Directory to save plot images
PLOTS_DIR = os.path.join(os.getcwd(), "plots")
os.makedirs(PLOTS_DIR, exist_ok=True)

# Rolling window in number of expected intervals
ROLLING_POINTS = 12

# Function to load entries from a log file
def load_entries(path, since_ts=None):
	data = []
	if not os.path.exists(path):
		print(f"File not found: {path}")
		return data

	with open(path, "r") as f:
		for line in f:
			parts = line.strip().split(",")
			# Expect exactly 5 parts: ts + 4 metrics
			if len(parts) != 5:
				continue
			ts_str, *val_strs = parts
			try:
				ts = int(ts_str)
				vals = list(map(float, val_strs))
			except ValueError:
				continue
			if since_ts is None or ts >= since_ts:
				data.append((ts, vals))
	return data

# Convert loaded entries to dictionaries per metric
def entries_to_series(entries):
	metrics = {
		"cpu_percent": [],
		"ram_used": [],
		"disk_used": [],
		"cpu_temp": []
	}
	times = []
	for ts, vals in entries:
		times.append(datetime.fromtimestamp(ts))
		for key, val in zip(metrics.keys(), vals):
			metrics[key].append(val)
	return times, metrics

# Compute rolling average over a time window determined by age and expected interval
def compute_time_based_rolling(times, values, now_ts, points):
	ts_list = [t.timestamp() for t in times]
	roll_times = []
	roll_vals = []

	for i, ts_i in enumerate(ts_list):
		age = now_ts - ts_i
		# Determine expected sampling interval based on age
		if age <= 3600:            # live data (last hour)
			expected = 5
		elif age <= 86400:         # compressed per minute
			expected = 60
		elif age <= 2592000:       # compressed per hour
			expected = 3600
		else:                      # compressed per day
			expected = 86400

		window_sec = expected * points
		start_time = ts_i - window_sec

		# Collect values within window [start_time, ts_i]
		window_vals = [
			v for j, v in enumerate(values)
			if start_time <= ts_list[j] <= ts_i
		]

		if window_vals:
			avg = sum(window_vals) / len(window_vals)
			roll_times.append(times[i])
			roll_vals.append(avg)

	return roll_times, roll_vals

# Plot a single metric (with optional rolling average) and save to file
def plot_single_metric(times, values, roll_times, roll_vals, filename, ylabel, title):
	fig, ax = plt.subplots(figsize=(10, 4))

	# First, break raw data into segments if there are large gaps
	segments_x = []
	segments_y = []
	curr_seg_x = [times[0]]
	curr_seg_y = [values[0]]
	now_ts = time.time()

	for i in range(1, len(times)):
		t_prev = times[i - 1]
		t_curr = times[i]
		ts_curr = t_curr.timestamp()
		age = now_ts - ts_curr

		# Determine expected sampling interval based on age
		if age <= 3600:            # last hour
			expected = 5
		elif age <= 86400:         # 1h–1d
			expected = 60
		elif age <= 2592000:       # 1d–30d
			expected = 3600
		else:                      # >30d
			expected = 86400

		diff = (t_curr - t_prev).total_seconds()
		# If gap exceeds twice expected, break the line
		if diff > expected * 2:
			segments_x.append(curr_seg_x)
			segments_y.append(curr_seg_y)
			curr_seg_x = [t_curr]
			curr_seg_y = [values[i]]
		else:
			curr_seg_x.append(t_curr)
			curr_seg_y.append(values[i])

	segments_x.append(curr_seg_x)
	segments_y.append(curr_seg_y)

	# Plot each continuous segment of raw data
	for xs, ys in zip(segments_x, segments_y):
		ax.plot(xs, ys, linewidth=1)

	# Overlay rolling average if given
	if roll_times and roll_vals:
		ax.plot(roll_times, roll_vals, linestyle="--", label="Rolling Avg")
		ax.legend()

	# Set labels and title
	ax.set_ylabel(ylabel)
	ax.set_title(title)

	# Format x-axis datetime ticks based on total span
	min_time = times[0]
	max_time = times[-1]
	span_days = (max_time - min_time).days

	if span_days < 1:  
		# If all points within same day → label as HH:MM
		fmt = mdates.DateFormatter("%H:%M")
	elif span_days <= 365:
		# Within the same calendar year → label as Month Day (e.g. "Jun 23")
		fmt = mdates.DateFormatter("%b %d")
	else:
		# Span more than one year → include year (e.g. "2023 Jun 23")
		fmt = mdates.DateFormatter("%Y %b %d")

	ax.xaxis.set_major_formatter(fmt)
	fig.autofmt_xdate(rotation=30, ha="right")

	plt.tight_layout()
	plt.savefig(filename)
	plt.close(fig)
	print(f"Saved plot: {filename}")

# Wrapper that, for a given time range, extracts each metric and calls plot_single_metric
def plot_for_range(entries, filename_prefix):
	times, metrics = entries_to_series(entries)
	now_ts = time.time()

	# Compute rolling averages for CPU% and CPU temp
	cpu_roll_times, cpu_roll_vals = compute_time_based_rolling(
		times, metrics["cpu_percent"], now_ts, ROLLING_POINTS
	)
	temp_roll_times, temp_roll_vals = compute_time_based_rolling(
		times, metrics["cpu_temp"], now_ts, ROLLING_POINTS
	)

	# Define the four metrics and their labels
	to_plot = [
		("cpu_percent", "CPU %", cpu_roll_times, cpu_roll_vals),
		("ram_used", "RAM Used (GiB)", [], []),
		("disk_used", "Disk Used (GiB)", [], []),
		("cpu_temp", "CPU Temp (°C)", temp_roll_times, temp_roll_vals)
	]

	for key, ylabel, r_times, r_vals in to_plot:
		values = metrics[key]
		filename = os.path.join(
			PLOTS_DIR,
			f"{filename_prefix}_{key}.png"
		)
		title = f"{ylabel} ({filename_prefix.replace('_', ' ').title()})"
		plot_single_metric(times, values, r_times, r_vals, filename, ylabel, title)

# Main function to plot different ranges and save
def plot_ranges():
	now_ts = int(time.time())

	# 1) Last hour from live log
	one_hour_ago = now_ts - 3600
	hour_entries = load_entries(LIVE_LOG_PATH, since_ts=one_hour_ago)
	if hour_entries:
		# Sort entries just in case
		hour_entries.sort(key=lambda e: e[0])
		plot_for_range(hour_entries, "last_hour")
	else:
		print("No live entries for the last hour.")

	# 2) Last day: combine live and compressed
	twenty_four_hours_ago = now_ts - 86400
	day_live = load_entries(LIVE_LOG_PATH, since_ts=twenty_four_hours_ago)
	day_comp = load_entries(COMPRESSED_LOG_PATH, since_ts=twenty_four_hours_ago)
	day_entries = day_live + day_comp
	if day_entries:
		day_entries.sort(key=lambda e: e[0])
		plot_for_range(day_entries, "last_day")
	else:
		print("No entries for the last day.")

	# 3) All available data (compressed log)
	all_entries = load_entries(COMPRESSED_LOG_PATH)
	if all_entries:
		all_entries.sort(key=lambda e: e[0])
		plot_for_range(all_entries, "all_data")
	else:
		print("No compressed entries available.")

# Example usage
if __name__ == "__main__":
	# Ensure the metrics and plots directories exist
	os.makedirs(LOG_DIR, exist_ok=True)
	os.makedirs(PLOTS_DIR, exist_ok=True)

	# Plot the ranges and save to files
	plot_ranges()
