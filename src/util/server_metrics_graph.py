import os
import time
from datetime import datetime
import matplotlib.pyplot as plt

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
        window_vals = [v for j, v in enumerate(values) if start_time <= ts_list[j] <= ts_i]
        
        if window_vals:
            avg = sum(window_vals) / len(window_vals)
            roll_times.append(times[i])
            roll_vals.append(avg)
    
    return roll_times, roll_vals

def plot_with_gaps_and_roll(ax, times, values, roll_times, roll_vals, now_ts):
    """
    Plot a time series but break the line on large gaps, and overlay rolling average.
    """
    segments_x = []
    segments_y = []
    curr_seg_x = [times[0]]
    curr_seg_y = [values[0]]

    for i in range(1, len(times)):
        t_prev = times[i - 1]
        t_curr = times[i]
        ts_curr = t_curr.timestamp()
        age = now_ts - ts_curr

        # Determine expected sampling interval based on age
        if age <= 3600:                      # last hour: live data
            expected = 5
        elif age <= 86400:                   # 1h–1d: compressed per minute
            expected = 60
        elif age <= 2592000:                 # 1d–30d: compressed per hour
            expected = 3600
        else:                                # >30d: compressed per day
            expected = 86400

        diff = (t_curr - t_prev).total_seconds()
        # If gap exceeds twice expected, break the segment
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

    # Plot each raw-data segment
    for xs, ys in zip(segments_x, segments_y):
        ax.plot(xs, ys)

    # Plot rolling average (as a dashed line)
    if roll_times and roll_vals:
        ax.plot(roll_times, roll_vals, linestyle="--", label="Rolling Avg")
        ax.legend()

# Plot metrics for a given set of entries and save to file
def plot_metrics(times, metrics, filename_suffix=""):
    now_ts = time.time()
    # Compute time-based rolling averages for CPU % and CPU temp
    cpu_roll_times, cpu_roll_vals = compute_time_based_rolling(times, metrics["cpu_percent"], now_ts, ROLLING_POINTS)
    temp_roll_times, temp_roll_vals = compute_time_based_rolling(times, metrics["cpu_temp"], now_ts, ROLLING_POINTS)

    fig, axs = plt.subplots(4, 1, figsize=(10, 12), sharex=True)

    plot_with_gaps_and_roll(axs[0], times, metrics["cpu_percent"], cpu_roll_times, cpu_roll_vals, now_ts)
    axs[0].set_ylabel("CPU %")
    axs[0].set_title(f"CPU Usage {filename_suffix}")

    plot_with_gaps_and_roll(axs[1], times, metrics["ram_used"], [], [], now_ts)
    axs[1].set_ylabel("RAM Used (GiB)")
    axs[1].set_title(f"RAM Usage {filename_suffix}")

    plot_with_gaps_and_roll(axs[2], times, metrics["disk_used"], [], [], now_ts)
    axs[2].set_ylabel("Disk Used (GiB)")
    axs[2].set_title(f"Disk Usage {filename_suffix}")

    plot_with_gaps_and_roll(axs[3], times, metrics["cpu_temp"], temp_roll_times, temp_roll_vals, now_ts)
    axs[3].set_ylabel("CPU Temp (°C)")
    axs[3].set_title(f"CPU Temperature {filename_suffix}")
    axs[3].set_xlabel("Time")

    plt.tight_layout()
    output_path = os.path.join(
        PLOTS_DIR,
        f"{filename_suffix.strip('()').replace(' ', '_').lower()}.png"
    )
    plt.savefig(output_path)
    plt.close(fig)
    print(f"Saved plot: {output_path}")

# Main function to plot different ranges and save
def plot_ranges():
    now_ts = int(time.time())

    # Last hour from live log
    one_hour_ago = now_ts - 3600
    hour_entries = load_entries(LIVE_LOG_PATH, since_ts=one_hour_ago)
    if hour_entries:
        hour_times, hour_metrics = entries_to_series(hour_entries)
        plot_metrics(hour_times, hour_metrics, filename_suffix="Last Hour")
    else:
        print("No live entries for the last hour.")

    # Last day: combine live and compressed
    twenty_four_hours_ago = now_ts - 86400
    day_live = load_entries(LIVE_LOG_PATH, since_ts=twenty_four_hours_ago)
    day_comp = load_entries(COMPRESSED_LOG_PATH, since_ts=twenty_four_hours_ago)
    day_entries = day_live + day_comp
    day_entries.sort(key=lambda e: e[0])
    if day_entries:
        day_times, day_metrics = entries_to_series(day_entries)
        plot_metrics(day_times, day_metrics, filename_suffix="Last Day")
    else:
        print("No entries for the last day.")

    # All available data (compressed log)
    all_entries = load_entries(COMPRESSED_LOG_PATH)
    if all_entries:
        all_times, all_metrics = entries_to_series(all_entries)
        plot_metrics(all_times, all_metrics, filename_suffix="All Data")
    else:
        print("No compressed entries available.")

# Example usage
if __name__ == "__main__":
    # Ensure the metrics and plots directories exist
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(PLOTS_DIR, exist_ok=True)

    # Plot the ranges and save to files
    plot_ranges()
