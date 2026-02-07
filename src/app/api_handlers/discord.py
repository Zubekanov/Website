from __future__ import annotations

import flask

from app.api_context import ApiContext
from app.api_common import (
	discord_interaction_response,
	handle_mod_action,
	verify_discord_signature,
)


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/discord/interactions", methods=["POST"])
	def api_discord_interactions():
		signature = flask.request.headers.get("X-Signature-Ed25519", "")
		timestamp = flask.request.headers.get("X-Signature-Timestamp", "")
		body = flask.request.get_data() or b""

		if not verify_discord_signature(signature, timestamp, body, ctx=ctx):
			return flask.jsonify({"error": "Invalid request signature."}), 401

		payload = flask.request.get_json(silent=True) or {}
		itype = payload.get("type")
		if itype == 1:
			return flask.jsonify({"type": 1})

		if itype == 3:
			data = payload.get("data") or {}
			custom_id = data.get("custom_id") or ""
			parts = custom_id.split(":")
			if len(parts) != 4 or parts[0] != "mod":
				return flask.jsonify(discord_interaction_response("Unsupported action.", ok=False))
			_, action, kind, reg_id = parts
			ok, message = handle_mod_action(ctx, kind, action, reg_id)
			return flask.jsonify(discord_interaction_response(message, ok=ok))

		return flask.jsonify(discord_interaction_response("Unsupported interaction type.", ok=False))
