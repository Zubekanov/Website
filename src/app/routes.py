import time
from flask import Blueprint, render_template

main = Blueprint('main', __name__)
start_time = time.time()

def format_uptime(seconds):
	units = [
		("y", 60 * 60 * 24 * 365),
		("d", 60 * 60 * 24),
		("h", 60 * 60),
		("m", 60),
		("s", 1),
	]

	result = []
	for name, count in units:
		value = seconds // count
		if value > 0 or result:
			result.append(f"{int(value)}{name}")
			seconds %= count
		if len(result) == 2:
			break

	return " ".join(result) if result else "0s"

@main.route("/")
def home():
	uptime_seconds = int(time.time() - start_time)
	return render_template("index.html", uptime_seconds=uptime_seconds)

@main.route("/server")
def server_details():
	return "This page will contain server details in the future."

@main.route("/api/uptime")
def api_uptime():
	uptime_seconds = int(time.time() - start_time)
	return {"uptime_seconds": uptime_seconds}

