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

@main.route("/api/server_overview")
def api_server_overview():
	pass
