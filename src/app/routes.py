import time
from flask import Blueprint, render_template
from app.layout_fetcher import LayoutFetcher

main = Blueprint('main', __name__)
start_time = time.time()

@main.route("/")
def homepage():
	components = LayoutFetcher.load_layout("homepage.json")
	uptime_seconds = int(time.time() - start_time)
	return render_template("main_layout.html", **components)

@main.route("/server")
def server_details():
	return "This page will contain server details in the future."

@main.route("/api/uptime")
def api_uptime():
	uptime_seconds = int(time.time() - start_time)
	return {"uptime_seconds": uptime_seconds}
