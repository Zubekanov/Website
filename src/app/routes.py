import time
from flask import Blueprint, render_template
from app.layout_fetcher import LayoutFetcher
from app.breadcrumbs import generate_breadcrumbs

main = Blueprint('main', __name__)
start_time = time.time()

@main.route("/")
def homepage():
	components = LayoutFetcher.load_layout("homepage.json")
	breadcrumbs = generate_breadcrumbs()
	return render_template("main_layout.html", **components, breadcrumbs=breadcrumbs)

@main.route("/server")
def server_overview():
	components = LayoutFetcher.load_layout("server_overview.json")
	breadcrumbs = generate_breadcrumbs()
	return render_template("main_layout.html", **components, breadcrumbs=breadcrumbs)

@main.route("/api/uptime")
def api_uptime():
	uptime_seconds = int(time.time() - start_time)
	return {"uptime_seconds": uptime_seconds}
