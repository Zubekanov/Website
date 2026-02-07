from __future__ import annotations

import flask
from datetime import datetime, timezone, timedelta

from app.api_context import ApiContext
from app.metrics_utils import normalize_metrics_query


def _metrics_names_and_units() -> tuple[list[str], dict]:
	from util.webpage_builder.metrics_builder import METRICS_NAMES, METRICS_UNITS
	return METRICS_NAMES, METRICS_UNITS


def _get_metrics(metric: str, num_entries: int, format_ts: bool):
	from util.webpage_builder.metrics_builder import get_metrics
	return get_metrics(metric, num_entries=num_entries, format_ts=format_ts)


def _get_metrics_bulk(metrics: list[str], num_entries: int, format_ts: bool):
	from util.webpage_builder.metrics_builder import get_metrics_bulk
	return get_metrics_bulk(metrics, num_entries=num_entries, format_ts=format_ts)


def _get_metrics_bucketed(metric: str, since_dt: datetime, bucket_seconds: int, format_ts: bool):
	from util.webpage_builder.metrics_builder import get_metrics_bucketed
	return get_metrics_bucketed(metric, since_dt=since_dt, bucket_seconds=bucket_seconds, format_ts=format_ts)


def _get_latest_metrics_entry(num_entries: int = 1):
	from util.webpage_builder.metrics_builder import _get_latest_metrics
	return _get_latest_metrics(num_entries=num_entries)


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/metrics/names")
	def api_metrics_names():
		metrics_names, metrics_units = _metrics_names_and_units()
		return flask.jsonify({
			"names": metrics_names,
			"units": metrics_units,
		})

	@api.route("/api/metrics/<metric>")
	def api_metrics(metric):
		count = flask.request.args.get("count", type=int)
		since = flask.request.args.get("since", type=str)
		window = flask.request.args.get("window", type=int)
		bucket = flask.request.args.get("bucket", type=int)
		format_ts = flask.request.args.get("format_ts", default="false", type=str).lower() == "true"

		query, error = normalize_metrics_query(
			count=count,
			since=since,
			window=window,
			bucket=bucket,
			format_ts=format_ts,
		)
		if error:
			return flask.jsonify({
				"error": error,
				"timestamps": [],
				"data": [],
			}), 400

		try:
			if query.window:
				if query.bucket is None:
					return flask.jsonify({
						"error": "bucket is required when window is specified.",
						"timestamps": [],
						"data": [],
					}), 400
				now = datetime.now(timezone.utc)
				since_dt = now - timedelta(seconds=query.window)
				timestamps, values = _get_metrics_bucketed(
					metric,
					since_dt=since_dt,
					bucket_seconds=query.bucket,
					format_ts=query.format_ts,
				)
			else:
				timestamps, values = _get_metrics(metric, num_entries=query.count, format_ts=query.format_ts)
		except ValueError as e:
			return flask.jsonify({
				"error": "Invalid metrics request.",
				"timestamps": [],
				"data": [],
			}), 400
		except Exception as e:
			return flask.jsonify({
				"error": "Unable to fetch metrics right now.",
				"timestamps": [],
				"data": [],
			}), 500

		return flask.jsonify({
			"error": None,
			"timestamps": timestamps,
			"data": values,
		})

	@api.route("/api/metrics/bulk")
	def api_metrics_bulk():
		metrics_param = flask.request.args.get("metrics", type=str) or ""
		metrics = [m.strip() for m in metrics_param.split(",") if m.strip()]
		count = flask.request.args.get("count", type=int)
		since = flask.request.args.get("since", type=str)
		window = flask.request.args.get("window", type=int)
		bucket = flask.request.args.get("bucket", type=int)
		format_ts = flask.request.args.get("format_ts", default="false", type=str).lower() == "true"

		if not metrics:
			return flask.jsonify({
				"error": "metrics parameter is required.",
				"timestamps": [],
				"data": {},
			}), 400

		query, error = normalize_metrics_query(
			count=count,
			since=since,
			window=window,
			bucket=bucket,
			format_ts=format_ts,
		)
		if error:
			return flask.jsonify({
				"error": error,
				"timestamps": [],
				"data": {},
			}), 400

		try:
			if query.window:
				if query.bucket is None:
					return flask.jsonify({
						"error": "bucket is required when window is specified.",
						"timestamps": [],
						"data": {},
					}), 400
				now = datetime.now(timezone.utc)
				since_dt = now - timedelta(seconds=query.window)
				data = {}
				timestamps = []
				for idx, metric in enumerate(metrics):
					try:
						ts, values = _get_metrics_bucketed(
							metric,
							since_dt=since_dt,
							bucket_seconds=query.bucket,
							format_ts=query.format_ts,
						)
					except Exception as metric_err:
						return flask.jsonify({
							"error": "Unable to fetch one or more metrics right now.",
							"timestamps": [],
							"data": {},
						}), 500
					if idx == 0:
						timestamps = ts
					data[metric] = values
				return flask.jsonify({
					"error": None,
					"timestamps": timestamps,
					"data": data,
				})
			else:
				timestamps, data = _get_metrics_bulk(
					metrics,
					num_entries=query.count,
					format_ts=query.format_ts,
				)
				return flask.jsonify({
					"error": None,
					"timestamps": timestamps,
					"data": data,
				})
		except ValueError as e:
			return flask.jsonify({
				"error": "Invalid metrics request.",
				"timestamps": [],
				"data": {},
			}), 400
		except Exception as e:
			return flask.jsonify({
				"error": "Unable to fetch metrics right now.",
				"timestamps": [],
				"data": {},
			}), 500

	@api.route("/api/metrics/update")
	def api_metrics_update():
		metrics = _get_latest_metrics_entry(num_entries=1)
		if not metrics:
			return flask.jsonify({
				"error": "No metrics data available.",
				"data": {},
			}), 500
		return flask.jsonify({
			"error": None,
			"data": metrics[0],
		})
