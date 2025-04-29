from flask import Blueprint, render_template
from app.layout_fetcher import LayoutFetcher
from app.breadcrumbs import generate_breadcrumbs
import util.server_metrics as metrics

main = Blueprint('main', __name__)

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
	return {"uptime_seconds": metrics.get_uptime()}

@main.route("/api/static_metrics")
def api_static_metrics():
	return metrics.get_static_metrics()

@main.route("/api/live_metrics")
def api_live_metrics():
	return metrics.get_latest_metrics()

@main.route("/api/hour_metrics")
def api_hour_metrics():
	return metrics.get_last_hour_metrics()

@main.route("/api/compressed_metrics")
def api_compressed_metrics():
	return metrics.get_compressed_metrics()