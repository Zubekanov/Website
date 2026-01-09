import logging
import flask
from util.webpage_builder.metrics_builder import *
import datetime
from datetime import timedelta

from util.webpage_builder.metrics_builder import _get_latest_metrics
from util.user_management import UserManagement

logger = logging.getLogger(__name__)
api = flask.Blueprint("api", __name__)

@api.route("/api/ping")
def api_ping():
	return flask.jsonify({"message": "pong"})

@api.route("/api/metrics/names")
def api_metrics_names():
	return flask.jsonify({
		"names": METRICS_NAMES,
		"units": METRICS_UNITS,
		})

@api.route("/api/metrics/<metric>")
def api_metrics(metric):
	# Takes count = number of entries or since = timestamp to get entries since
	count = flask.request.args.get("count", type=int)
	since = flask.request.args.get("since", type=str)
	format_ts = flask.request.args.get("format_ts", default="false", type=str).lower() == "true"
	
	if count and since:
		return flask.jsonify({
			"error": "Do not specify both 'count' and 'since' parameters.",
			"timestamps": [],
			"data": [],
			}), 400
	
	if not count and not since:
		count = 720

	# Currently do not support since that is more than one hour ago
	if since:
		try:
			since_dt = datetime.datetime.fromisoformat(since)
		except ValueError:
			return flask.jsonify({
				"error": "Invalid 'since' timestamp format. Use ISO 8601 format.",
				"timestamps": [],
				"data": [],
			}), 400

		one_hour_ago = datetime.datetime.now(datetime.timezone.utc) - timedelta(hours=1)
		if since_dt < one_hour_ago:
			count = 720
		else:
			# Round since down to nearest 5 seconds and calculate count
			since_dt = since_dt.replace(microsecond=0)
			since_dt = since_dt - timedelta(seconds=since_dt.second % 5)
			delta = datetime.datetime.now(datetime.timezone.utc) - since_dt
			count = int(delta.total_seconds() / 5) + 1

	try:
		timestamps, values = get_metrics(metric, num_entries=count, format_ts=format_ts)
	except ValueError as e:
		return flask.jsonify({
			"error": str(e),
			"timestamps": [],
			"data": [],
			}), 400

	metrics = flask.jsonify({
		"error": None,
		"timestamps": timestamps,
		"data": values,
	})
	return metrics

@api.route("/api/metrics/update")
def api_metrics_update():
	metrics = _get_latest_metrics(num_entries=1)
	if not metrics:
		return flask.jsonify({
			"error": "No metrics data available.",
			"data": {},
		}), 500
	return flask.jsonify({
		"error": None,
		"data": metrics[0],
	})

@api.route("/login", methods=["POST"])
def api_login():
	print(flask.request.json)
	return (
		flask.jsonify({
			"ok": False,
			"message": "Login is not implemented yet.",
		}),
		501,
	)

@api.route("/register", methods=["POST"])
def api_register():
	print(flask.request.json)
	validation = UserManagement.validate_registration_fields(
		referral_source=flask.request.json.get("referral_source", ""),
		first_name=flask.request.json.get("first_name", ""),
		last_name=flask.request.json.get("last_name", ""),
		email=flask.request.json.get("email", ""),
		password=flask.request.json.get("password", ""),
		repeat_password=flask.request.json.get("repeat_password", ""),
	)
	return (
		flask.jsonify({
			"ok": validation[0],
			"message": validation[1],
		}),
		200 if validation[0] else 400,
	)

@api.route("/audiobookshelf-registration", methods=["POST"])
def api_audiobookshelf_registration():
	print(flask.request.json)
	return (
		flask.jsonify({
			"ok": False,
			"message": "Audiobookshelf registration is not implemented yet.",
		}),
		501,
	)
