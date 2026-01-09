import logging
import time
import flask
from flask import g
from util.webpage_builder.webpage_builder import *
from util.webpage_builder.metrics_builder import *
from bokeh.embed import server_document

logger = logging.getLogger(__name__)
main = flask.Blueprint("main", __name__)

@main.before_app_request
def _timing_start():
	g._t0 = time.perf_counter()

@main.after_app_request
def _timing_end(resp):
	if not hasattr(g, "_t0"):
		return resp

	ct = resp.headers.get("Content-Type", "")
	if "text/html" not in ct:
		return resp

	total_ms = (time.perf_counter() - g._t0) * 1000.0
	body = resp.get_data(as_text=True)
	body = body.replace("__BUILD_MS__", f"{total_ms:.1f} ms")
	body = body.replace("__BUILD_MS_CACHED__", f"{total_ms:.1f} ms (cached)")
	resp.set_data(body)
	return resp

@main.route("/")
def landing_page():
    return build_test_page()

@main.route("/server-metrics")
def server_metrics_page():
	return build_server_metrics_page()

@main.route("/login")
def login_page():
	return build_login_page()

@main.route("/register")
def register_page():
    return build_register_page()

@main.route("/audiobookshelf-registration", methods=["GET"])
def audiobookshelf_registration_page():
	return build_audiobookshelf_registration_page()

@main.app_errorhandler(404)
def page_not_found(e):
    return build_4xx_page(e), 404
