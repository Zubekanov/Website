from __future__ import annotations

import flask

from app.api_context import ApiContext


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/ping")
	def api_ping():
		return flask.jsonify({"message": "pong"})
