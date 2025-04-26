import time
import markdown
from flask import Blueprint, render_template
from util.configreader import ConfigReader

main = Blueprint('main', __name__)
start_time = time.time()

@main.route("/")
def home():
	# Fetch time for uptime badge.
	uptime_seconds = int(time.time() - start_time)
	# Fetch markdown contents for homepage.
	md_path = ConfigReader.get_content_file("homepage.md")
	with open(md_path, "r") as f:
		markdown_content = f.read()
	html_content = markdown.markdown(markdown_content)
	return render_template("index.html", uptime_seconds=uptime_seconds, page_content=html_content)

@main.route("/server")
def server_details():
	return "This page will contain server details in the future."

@main.route("/api/uptime")
def api_uptime():
	uptime_seconds = int(time.time() - start_time)
	return {"uptime_seconds": uptime_seconds}
