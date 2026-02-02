import logging
import os
import flask
from util.webpage_builder.metrics_builder import *
from util.webpage_builder.metrics_builder import get_metrics_bucketed
from util.integrations.discord.webhook_interface import DiscordWebhookEmitter
import secrets
import hashlib
import hmac
import bcrypt
import time
from datetime import datetime, timezone, timedelta
import threading
import uuid

from util.webpage_builder.metrics_builder import _get_latest_metrics
from util.user_management import UserManagement
from sql.psql_interface import PSQLInterface
from util.fcr.file_config_reader import FileConfigReader
from util.integrations.email.email_interface import render_template, send_email

logger = logging.getLogger(__name__)
api = flask.Blueprint("api", __name__)
interface = PSQLInterface()
fcr = FileConfigReader()

_AUTH_TOKEN_NAME_ = "session"
_MINECRAFT_STATUS_CACHE: dict[str, object] = {
	"data": None,
	"fetched_at_ts": None,
	"refreshing": False,
}
_MINECRAFT_STATUS_LOCK = threading.Lock()

def _get_or_create_anonymous_user(*, first_name: str, last_name: str, email: str) -> tuple[bool, str | None]:
	email_norm = (email or "").strip().lower()
	first_norm = (first_name or "").strip()
	last_norm = (last_name or "").strip()
	if not email_norm or not first_norm or not last_norm:
		return False, "First name, last name, and email are required."

	# Ensure schema supports anonymous users.
	try:
		cols = interface.client.get_column_info("public", "users")
		if "is_anonymous" not in cols:
			interface.client.add_column("public", "users", "is_anonymous", "boolean DEFAULT false NOT NULL")
		if cols.get("password_hash") and str(cols["password_hash"].get("is_nullable", "")).upper() != "YES":
			interface.client.alter_column_nullability("public", "users", "password_hash", nullable=True)
	except Exception as e:
		return False, f"Failed to prepare users schema: {e}"

	try:
		try:
			rows = interface.client.execute_query(
				"SELECT id, first_name, last_name, is_anonymous FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1;",
				(email_norm,),
			) or []
		except Exception:
			rows = interface.client.execute_query(
				"SELECT id, first_name, last_name FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1;",
				(email_norm,),
			) or []
		if rows:
			row = rows[0]
			if "is_anonymous" not in row or not row.get("is_anonymous"):
				return False, "It looks like you already have an account. Please log in."
			if row.get("first_name") and row.get("last_name"):
				if str(row["first_name"]).strip().lower() == first_norm.lower() and str(row["last_name"]).strip().lower() == last_norm.lower():
					return True, str(row["id"])
			return False, "An anonymous account with this email exists under a different name. Please log in."

		row = interface.client.insert_row("users", {
			"id": str(uuid.uuid4()),
			"email": email_norm,
			"first_name": first_norm,
			"last_name": last_norm,
			"password_hash": None,
			"is_active": True,
			"is_anonymous": True,
		})
		return True, str(row["id"])
	except Exception as e:
		return False, f"Failed to store anonymous user: {e}"


def _discord_public_key() -> str | None:
	try:
		conf = fcr.find("secrets.conf")
		key = conf.get("DISCORD_WEBHOOK_INTERACTIONS_PUBLIC_KEY") if conf else None
		return key.strip() if key else None
	except Exception:
		return None


def _verify_discord_signature(signature: str, timestamp: str, body: bytes) -> bool:
	try:
		from nacl.signing import VerifyKey
		from nacl.exceptions import BadSignatureError
	except Exception:
		return False
	key = _discord_public_key()
	if not key or not signature or not timestamp:
		return False
	try:
		verify_key = VerifyKey(bytes.fromhex(key))
		verify_key.verify(timestamp.encode("utf-8") + body, bytes.fromhex(signature))
		return True
	except BadSignatureError:
		return False
	except Exception:
		return False


def _discord_interaction_response(content: str, *, ok: bool = True) -> dict:
	return {
		"type": 4,
		"data": {
			"content": content,
			"flags": 64,
		},
	}


def _handle_mod_action(kind: str, action: str, reg_id: str) -> tuple[bool, str]:
	if kind == "audiobookshelf":
		if action == "approve":
			try:
				interface.client.update_rows_with_filters(
					"audiobookshelf_registrations",
					{
						"status": "approved",
						"is_active": True,
						"reviewed_at": datetime.now(timezone.utc),
					},
					raw_conditions=["id = %s"],
					raw_params=[reg_id],
				)
				return True, "Audiobookshelf request approved."
			except Exception as e:
				return False, f"Approve failed: {e}"
		if action == "deny":
			try:
				interface.client.update_rows_with_filters(
					"audiobookshelf_registrations",
					{
						"status": "denied",
						"is_active": False,
						"reviewed_at": datetime.now(timezone.utc),
					},
					raw_conditions=["id = %s"],
					raw_params=[reg_id],
				)
				return True, "Audiobookshelf request denied."
			except Exception as e:
				return False, f"Deny failed: {e}"
	elif kind == "discord-webhook":
		if action == "approve":
			emitter = DiscordWebhookEmitter(interface)
			return emitter.approve_registration(registration_id=reg_id, reviewer_user_id=None)
		if action == "deny":
			try:
				interface.client.update_rows_with_filters(
					"discord_webhook_registrations",
					{
						"status": "denied",
						"reviewed_at": datetime.now(timezone.utc),
					},
					raw_conditions=["id = %s"],
					raw_params=[reg_id],
				)
				return True, "Discord webhook request denied."
			except Exception as e:
				return False, f"Deny failed: {e}"
	elif kind == "minecraft":
		if action == "approve":
			try:
				rows = interface.client.execute_query(
					"SELECT * FROM minecraft_registrations WHERE id = %s;",
					(reg_id,),
				) or []
				if rows:
					reg = rows[0]
					existing = interface.client.execute_query(
						"SELECT id, is_active FROM minecraft_whitelist WHERE LOWER(mc_username) = LOWER(%s) LIMIT 1;",
						(reg.get("mc_username"),),
					) or []
					if not existing:
						interface.client.insert_row("minecraft_whitelist", {
							"user_id": reg.get("user_id"),
							"first_name": reg.get("first_name"),
							"last_name": reg.get("last_name"),
							"email": reg.get("email"),
							"mc_username": reg.get("mc_username"),
							"is_active": True,
						})
					else:
						interface.client.update_rows_with_filters(
							"minecraft_whitelist",
							{"is_active": True, "ban_reason": None},
							raw_conditions=["id = %s"],
							raw_params=[existing[0]["id"]],
						)
				interface.client.update_rows_with_filters(
					"minecraft_registrations",
					{
						"status": "approved",
						"reviewed_at": datetime.now(timezone.utc),
					},
					raw_conditions=["id = %s"],
					raw_params=[reg_id],
				)
				return True, "Minecraft request approved."
			except Exception as e:
				return False, f"Approve failed: {e}"
		if action == "deny":
			try:
				interface.client.update_rows_with_filters(
					"minecraft_registrations",
					{
						"status": "denied",
						"reviewed_at": datetime.now(timezone.utc),
					},
					raw_conditions=["id = %s"],
					raw_params=[reg_id],
				)
				return True, "Minecraft request denied."
			except Exception as e:
				return False, f"Deny failed: {e}"

	return False, "Unsupported action."


def _notify_moderators(
	action: str,
	*,
	title: str,
	actor: str | None = None,
	subject: str | None = None,
	details: list[str] | None = None,
	actions: list[tuple[str, str]] | None = None,
	buttons: list[dict] | None = None,
	context: dict | None = None,
) -> None:
	"""
	Send a moderator.notifications event; failures should not break requests.
	Standard payload uses an embed with action/actor/subject fields.
	"""
	try:
		fields = [{"name": "Action", "value": action, "inline": True}]
		if actor:
			fields.append({"name": "Actor", "value": actor, "inline": True})
		if subject:
			fields.append({"name": "Subject", "value": subject, "inline": True})
		if details:
			detail_text = "\n".join(f"- {line}" for line in details if line)
			if detail_text:
				fields.append({"name": "Details", "value": detail_text, "inline": False})
		if actions:
			action_text = " | ".join(f"[{label}]({url})" for label, url in actions if label and url)
			if action_text:
				fields.append({"name": "Actions", "value": action_text, "inline": False})

		emitter = DiscordWebhookEmitter(interface)
		payload = {
			"embeds": [
				{
					"title": title,
					"fields": fields,
				}
			]
		}
		if buttons:
			payload["components"] = [
				{
					"type": 1,
					"components": buttons[:5],
				}
			]

		emitter.emit_event(
			"moderator.notifications",
			payload=payload,
			context=context or {},
		)
	except Exception as e:
		logger.warning("Failed to emit moderator notification: %s", e)

def _build_admin_action_links(kind: str, reg_id: str) -> list[tuple[str, str]]:
	base = flask.request.host_url.rstrip("/")
	if kind == "audiobookshelf":
		return [
			("Approve", f"{base}/api/admin/audiobookshelf/approve-link?id={reg_id}"),
			("Deny", f"{base}/api/admin/audiobookshelf/deny-link?id={reg_id}"),
		]
	if kind == "discord-webhook":
		return [
			("Approve", f"{base}/api/admin/discord-webhook/approve-link?id={reg_id}"),
			("Deny", f"{base}/api/admin/discord-webhook/deny-link?id={reg_id}"),
		]
	if kind == "minecraft":
		return [
			("Approve", f"{base}/api/admin/minecraft/approve-link?id={reg_id}"),
			("Deny", f"{base}/api/admin/minecraft/deny-link?id={reg_id}"),
		]
	return []

def _build_admin_action_buttons(kind: str, reg_id: str) -> list[dict]:
	if not kind or not reg_id:
		return []
	return [
		{
			"type": 2,
			"style": 3,
			"label": "Approve",
			"custom_id": f"mod:approve:{kind}:{reg_id}",
		},
		{
			"type": 2,
			"style": 4,
			"label": "Deny",
			"custom_id": f"mod:deny:{kind}:{reg_id}",
		},
	]

@api.route("/api/ping")
def api_ping():
	return flask.jsonify({"message": "pong"})

@api.route("/api/profile/discord-webhook/unsubscribe", methods=["POST"])
def api_profile_discord_webhook_unsubscribe():
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if not token:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401
	user = UserManagement.get_user_by_session_token(token)
	if not user:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401

	data = flask.request.json or {}
	sub_id = (data.get("subscription_id") or data.get("id") or "").strip()
	if not sub_id:
		return flask.jsonify({"ok": False, "message": "Missing subscription id."}), 400

	try:
		rows = interface.client.execute_query(
			"SELECT s.id FROM discord_webhook_subscriptions s "
			"JOIN discord_webhooks w ON w.id = s.webhook_id "
			"WHERE s.id = %s AND w.user_id = %s LIMIT 1;",
			(sub_id, user.get("id")),
		) or []
		if not rows:
			return flask.jsonify({"ok": False, "message": "Subscription not found."}), 404

		interface.client.update_rows_with_filters(
			"discord_webhook_subscriptions",
			{"is_active": False},
			raw_conditions=["id = %s"],
			raw_params=[sub_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Failed to unsubscribe: {e}"}), 400

	return flask.jsonify({"ok": True})


@api.route("/api/profile/discord-webhook/resubscribe", methods=["POST"])
def api_profile_discord_webhook_resubscribe():
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if not token:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401
	user = UserManagement.get_user_by_session_token(token)
	if not user:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401

	data = flask.request.json or {}
	sub_id = (data.get("subscription_id") or data.get("id") or "").strip()
	if not sub_id:
		return flask.jsonify({"ok": False, "message": "Missing subscription id."}), 400

	try:
		rows = interface.client.execute_query(
			"SELECT s.id, w.is_active AS webhook_active "
			"FROM discord_webhook_subscriptions s "
			"JOIN discord_webhooks w ON w.id = s.webhook_id "
			"WHERE s.id = %s AND w.user_id = %s LIMIT 1;",
			(sub_id, user.get("id")),
		) or []
		if not rows:
			return flask.jsonify({"ok": False, "message": "Subscription not found."}), 404
		if not rows[0].get("webhook_active", True):
			return flask.jsonify({"ok": False, "message": "Webhook is inactive. Reactivate it first."}), 403

		interface.client.update_rows_with_filters(
			"discord_webhook_subscriptions",
			{"is_active": True},
			raw_conditions=["id = %s"],
			raw_params=[sub_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Failed to resubscribe: {e}"}), 400

	return flask.jsonify({"ok": True})


@api.route("/api/profile/integration/delete", methods=["POST"])
def api_profile_integration_delete():
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if not token:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401
	user = UserManagement.get_user_by_session_token(token)
	if not user:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401

	data = flask.request.json or {}
	integration_type = (data.get("integration_type") or "").strip().lower()
	integration_id = (data.get("integration_id") or "").strip()
	reason = (data.get("reason") or "").strip()
	confirmed = bool(data.get("confirm"))
	if not integration_type or not integration_id:
		return flask.jsonify({"ok": False, "message": "Missing integration details."}), 400
	if not confirmed:
		return flask.jsonify({"ok": False, "message": "Please confirm deletion."}), 400
	if not reason:
		return flask.jsonify({"ok": False, "message": "Please select a reason."}), 400

	try:
		if integration_type == "discord_webhook":
			rows = interface.client.execute_query(
				"SELECT id FROM discord_webhooks WHERE id = %s AND user_id = %s LIMIT 1;",
				(integration_id, user.get("id")),
			) or []
			if not rows:
				return flask.jsonify({"ok": False, "message": "Webhook not found."}), 404
			interface.client.update_rows_with_filters(
				"discord_webhooks",
				{"is_active": False},
				raw_conditions=["id = %s"],
				raw_params=[integration_id],
			)
			interface.client.delete_rows_with_filters(
				"application_exemptions",
				raw_conditions=["user_id = %s", "integration_type = 'discord_webhook'"],
				raw_params=[user.get("id")],
			)
			interface.client.insert_row("application_exemptions", {
				"user_id": user.get("id"),
				"integration_type": "discord_webhook",
				"integration_key": None,
			})
			_notify_moderators(
				"integration_disabled",
				title="Integration disabled by user",
				actor=user.get("email") or user.get("id"),
				subject="Discord Webhook",
				details=[
					f"Integration ID: {integration_id}",
					f"Reason: {reason}",
				],
				context={
					"action": "integration_disabled",
					"integration_type": "discord_webhook",
					"integration_id": integration_id,
					"user_id": user.get("id"),
					"reason": reason,
				},
			)
			return flask.jsonify({"ok": True, "message": "Webhook disabled."})
		if integration_type == "minecraft":
			rows = interface.client.execute_query(
				"SELECT id, ban_reason FROM minecraft_whitelist WHERE id = %s AND user_id = %s LIMIT 1;",
				(integration_id, user.get("id")),
			) or []
			if not rows:
				return flask.jsonify({"ok": False, "message": "Minecraft whitelist entry not found."}), 404
			mc_rows = interface.client.execute_query(
				"SELECT mc_username FROM minecraft_whitelist WHERE id = %s LIMIT 1;",
				(integration_id,),
			) or []
			mc_username = mc_rows[0].get("mc_username") if mc_rows else None
			existing_reason = (rows[0].get("ban_reason") or "").strip()
			note = "Account whitelisting disabled from user profile; enable by reapplying."
			combined_reason = existing_reason
			if note not in existing_reason:
				combined_reason = (existing_reason + "\n" + note).strip() if existing_reason else note
			interface.client.update_rows_with_filters(
				"minecraft_whitelist",
				{"is_active": False, "ban_reason": combined_reason},
				raw_conditions=["id = %s"],
				raw_params=[integration_id],
			)
			interface.client.delete_rows_with_filters(
				"application_exemptions",
				raw_conditions=["user_id = %s", "integration_type = 'minecraft'", "integration_key = %s"],
				raw_params=[user.get("id"), mc_username],
			)
			interface.client.insert_row("application_exemptions", {
				"user_id": user.get("id"),
				"integration_type": "minecraft",
				"integration_key": mc_username,
			})
			_notify_moderators(
				"integration_disabled",
				title="Integration disabled by user",
				actor=user.get("email") or user.get("id"),
				subject="Minecraft",
				details=[
					f"Integration ID: {integration_id}",
					f"Reason: {reason}",
				],
				context={
					"action": "integration_disabled",
					"integration_type": "minecraft",
					"integration_id": integration_id,
					"user_id": user.get("id"),
					"reason": reason,
				},
			)
			return flask.jsonify({"ok": True, "message": "Minecraft integration disabled."})
		if integration_type == "audiobookshelf":
			rows = interface.client.execute_query(
				"SELECT id FROM audiobookshelf_registrations WHERE id = %s AND user_id = %s LIMIT 1;",
				(integration_id, user.get("id")),
			) or []
			if not rows:
				return flask.jsonify({"ok": False, "message": "Audiobookshelf integration not found."}), 404
			interface.client.update_rows_with_filters(
				"audiobookshelf_registrations",
				{"is_active": False},
				raw_conditions=["id = %s"],
				raw_params=[integration_id],
			)
			interface.client.delete_rows_with_filters(
				"application_exemptions",
				raw_conditions=["user_id = %s", "integration_type = 'audiobookshelf'"],
				raw_params=[user.get("id")],
			)
			interface.client.insert_row("application_exemptions", {
				"user_id": user.get("id"),
				"integration_type": "audiobookshelf",
				"integration_key": None,
			})
			_notify_moderators(
				"integration_disabled",
				title="Integration disabled by user",
				actor=user.get("email") or user.get("id"),
				subject="Audiobookshelf",
				details=[
					f"Integration ID: {integration_id}",
					f"Reason: {reason}",
				],
				context={
					"action": "integration_disabled",
					"integration_type": "audiobookshelf",
					"integration_id": integration_id,
					"user_id": user.get("id"),
					"reason": reason,
				},
			)
			return flask.jsonify({"ok": True, "message": "Audiobookshelf integration disabled."})
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Failed to delete integration: {e}"}), 400

	return flask.jsonify({"ok": False, "message": "Unknown integration type."}), 400


@api.route("/api/profile/change-password", methods=["POST"])
def api_profile_change_password():
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if not token:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401
	user = UserManagement.get_user_by_session_token(token)
	if not user:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401

	data = flask.request.json or {}
	password = (data.get("password") or "").strip()
	confirm = (data.get("confirm_password") or "").strip()
	if not password or not confirm:
		return flask.jsonify({"ok": False, "message": "Please fill out both password fields."}), 400
	if password != confirm:
		return flask.jsonify({"ok": False, "message": "Passwords do not match."}), 400

	ok, msg = interface.update_user_password(user.get("id"), password)
	if not ok:
		return flask.jsonify({"ok": False, "message": msg}), 400
	return flask.jsonify({"ok": True, "message": "Password updated."})

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
	window = flask.request.args.get("window", type=int)
	bucket = flask.request.args.get("bucket", type=int)
	format_ts = flask.request.args.get("format_ts", default="false", type=str).lower() == "true"
	
	if count and since:
		return flask.jsonify({
			"error": "Do not specify both 'count' and 'since' parameters.",
			"timestamps": [],
			"data": [],
			}), 400

	if window and since:
		return flask.jsonify({
			"error": "Do not specify both 'window' and 'since' parameters.",
			"timestamps": [],
			"data": [],
		}), 400
	
	if not count and not since:
		count = 720

	# Currently do not support since that is more than one hour ago
	if since:
		try:
			since_dt = datetime.fromisoformat(since)
		except ValueError:
			return flask.jsonify({
				"error": "Invalid 'since' timestamp format. Use ISO 8601 format.",
				"timestamps": [],
				"data": [],
			}), 400

		one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
		if since_dt < one_hour_ago:
			count = 720
		else:
			# Round since down to nearest 5 seconds and calculate count
			since_dt = since_dt.replace(microsecond=0)
			since_dt = since_dt - timedelta(seconds=since_dt.second % 5)
			delta = datetime.now(timezone.utc) - since_dt
			count = int(delta.total_seconds() / 5) + 1

	try:
		if window:
			if bucket is None:
				return flask.jsonify({
					"error": "bucket is required when window is specified.",
					"timestamps": [],
					"data": [],
				}), 400
			now = datetime.now(timezone.utc)
			since_dt = now - timedelta(seconds=window)
			timestamps, values = get_metrics_bucketed(
				metric,
				since_dt=since_dt,
				bucket_seconds=bucket,
				format_ts=format_ts,
			)
		else:
			timestamps, values = get_metrics(metric, num_entries=count, format_ts=format_ts)
	except ValueError as e:
		return flask.jsonify({
			"error": str(e),
			"timestamps": [],
			"data": [],
			}), 400
	except Exception as e:
		return flask.jsonify({
			"error": f"Failed to fetch metrics: {e}",
			"timestamps": [],
			"data": [],
		}), 500

	metrics = flask.jsonify({
		"error": None,
		"timestamps": timestamps,
		"data": values,
	})
	return metrics

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

	if count and since:
		return flask.jsonify({
			"error": "Do not specify both 'count' and 'since' parameters.",
			"timestamps": [],
			"data": {},
		}), 400

	if window and since:
		return flask.jsonify({
			"error": "Do not specify both 'window' and 'since' parameters.",
			"timestamps": [],
			"data": {},
		}), 400

	if not count and not since:
		count = 720

	if since:
		try:
			since_dt = datetime.fromisoformat(since)
		except ValueError:
			return flask.jsonify({
				"error": "Invalid 'since' timestamp format. Use ISO 8601 format.",
				"timestamps": [],
				"data": {},
			}), 400

		one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
		if since_dt < one_hour_ago:
			count = 720
		else:
			since_dt = since_dt.replace(microsecond=0)
			since_dt = since_dt - timedelta(seconds=since_dt.second % 5)
			delta = datetime.now(timezone.utc) - since_dt
			count = int(delta.total_seconds() / 5) + 1

	try:
		if window:
			if bucket is None:
				return flask.jsonify({
					"error": "bucket is required when window is specified.",
					"timestamps": [],
					"data": {},
				}), 400
			now = datetime.now(timezone.utc)
			since_dt = now - timedelta(seconds=window)
			data = {}
			timestamps = []
			for idx, metric in enumerate(metrics):
				try:
					ts, values = get_metrics_bucketed(
						metric,
						since_dt=since_dt,
						bucket_seconds=bucket,
						format_ts=format_ts,
					)
				except Exception as metric_err:
					return flask.jsonify({
						"error": f"Failed to fetch metric '{metric}': {metric_err}",
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
			timestamps, data = get_metrics_bulk(
				metrics,
				num_entries=count,
				format_ts=format_ts,
			)
			return flask.jsonify({
				"error": None,
				"timestamps": timestamps,
				"data": data,
			})
	except ValueError as e:
		return flask.jsonify({
			"error": str(e),
			"timestamps": [],
			"data": {},
		}), 400
	except Exception as e:
		return flask.jsonify({
			"error": f"Failed to fetch metrics: {e}",
			"timestamps": [],
			"data": {},
		}), 500
	return flask.jsonify({
		"error": "Unexpected error.",
		"timestamps": [],
		"data": {},
	}), 500

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
	validation, message = UserManagement.login_user(
		email=flask.request.json.get("email", ""),
		password=flask.request.json.get("password", ""),
		remember_me=flask.request.json.get("remember_me", False),
		ip=flask.request.remote_addr or "",
		user_agent=flask.request.headers.get("User-Agent", ""),
	)

	if validation:
		token = message
		message = "Login successful."
		# Set session cookie
	else:
		return (
			flask.jsonify({
				"ok": False,
				"message": message,
			}),
			401,
		)
	
	resp = flask.make_response(flask.jsonify({
		"ok": True,
		"message": message,
	}))

	resp.set_cookie(
		key = _AUTH_TOKEN_NAME_,
		value = token,
		httponly = True,
		secure = True,
		samesite = "Lax",
		max_age = 30 * 24 * 60 * 60 if flask.request.json.get("remember_me", False) else 24 * 60 * 60,
		path = "/",
	)

	return resp, 200

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
	if validation[0]:
		email = (flask.request.json.get("email", "") or "").strip().lower()
		first_name = (flask.request.json.get("first_name", "") or "").strip()
		last_name = (flask.request.json.get("last_name", "") or "").strip()
		referral_source = (flask.request.json.get("referral_source", "") or "").strip()
		_notify_moderators(
			"account_registration_submitted",
			title="New account registration submitted",
			actor="Anonymous",
			subject=f"{first_name} {last_name}".strip() or email,
			details=[
				f"Email: {email}" if email else "",
				f"Referral: {referral_source}" if referral_source else "",
			],
			context={
				"action": "account_registration_submitted",
				"email": email,
			},
		)
	return (
		flask.jsonify({
			"ok": validation[0],
			"message": validation[1],
		}),
		200 if validation[0] else 400,
	)


def _require_admin():
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if not token:
		return None, (flask.jsonify({"ok": False, "message": "Authentication required."}), 401)
	user = UserManagement.get_user_by_session_token(token)
	if not user:
		return None, (flask.jsonify({"ok": False, "message": "Invalid session."}), 401)
	if not interface.is_admin(user.get("id")):
		return None, (flask.jsonify({"ok": False, "message": "Admin access required."}), 403)
	return user, None


def _parse_db_value(value):
	if isinstance(value, str):
		val = value.strip()
		if val == "":
			return None
		low = val.lower()
		if low == "null":
			return None
		if low == "true":
			return True
		if low == "false":
			return False
	return value


def _get_table_meta(schema: str, table: str):
	if table not in interface.client.list_tables(schema):
		raise ValueError("Unknown table.")
	columns = interface.client.get_table_columns(schema, table)
	pk_cols = interface.client.get_primary_key_columns(schema, table)
	return columns, pk_cols


@api.route("/api/admin/email/debug", methods=["POST"])
def api_admin_send_debug_email():
	user, err = _require_admin()
	if err:
		return err

	data = flask.request.json or {}
	to_email = (data.get("to_email") or "").strip()
	send_verify = bool(data.get("send_verify"))
	verify_code = (data.get("verify_code") or "").strip()
	subject = (data.get("subject") or "").strip()
	body = (data.get("body") or "").strip()

	if not to_email or "@" not in to_email:
		return flask.jsonify({"ok": False, "message": "Valid recipient email is required."}), 400

	if send_verify:
		try:
			dummy_code = verify_code or "debug-token"
			base_url = (os.environ.get("WEBSITE_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").strip()
			if not base_url:
				try:
					conf = fcr.find("secrets.conf")
					if isinstance(conf, dict):
						for key in ("WEBSITE_BASE_URL", "PUBLIC_BASE_URL", "BASE_URL"):
							val = (conf.get(key) or "").strip()
							if val:
								base_url = val
								break
				except Exception:
					base_url = ""
			if not base_url:
				base_url = "http://localhost:5000"
			verify_url = f"{base_url.rstrip('/')}/verify-email/{dummy_code}"

			html_payload = render_template("verify_email.html", {"verify_url": verify_url})
			text_payload = (
				"Thanks for creating an account.\n\n"
				f"Verify your email: {verify_url}\n\n"
				"If you did not create this account, you can ignore this email.\n"
			)

			result = send_email(
				to_addrs=[to_email],
				subject="Verify your email",
				body_text=text_payload,
				body_html=html_payload,
			)
		except Exception as exc:
			logger.exception("Failed to send verification debug email for admin user_id=%s", user.get("id"))
			return flask.jsonify({"ok": False, "message": f"Send failed: {exc}"}), 500

		if not result.ok:
			return flask.jsonify({"ok": False, "message": f"Send failed: {result.error}"}), 502
		return flask.jsonify({"ok": True, "message": "Verification email sent."}), 200

	if not subject:
		return flask.jsonify({"ok": False, "message": "Subject is required."}), 400
	if not body:
		return flask.jsonify({"ok": False, "message": "Body is required."}), 400

	try:
		import html as _html

		subject_html = _html.escape(subject)
		body_html = _html.escape(body).replace("\n", "<br>")
		html_payload = render_template("debug_email.html", {
			"subject": subject_html,
			"body": body_html,
		})
		text_payload = f"{subject}\n\n{body}"
		result = send_email(
			to_addrs=[to_email],
			subject=subject,
			body_text=text_payload,
			body_html=html_payload,
		)
	except Exception as exc:
		logger.exception("Failed to send debug email for admin user_id=%s", user.get("id"))
		return flask.jsonify({"ok": False, "message": f"Send failed: {exc}"}), 500

	if not result.ok:
		return flask.jsonify({"ok": False, "message": f"Send failed: {result.error}"}), 502

	return flask.jsonify({"ok": True, "message": "Debug email sent."}), 200

	return flask.jsonify({"ok": False, "message": "Unhandled email debug state."}), 500


@api.route("/api/admin/db/update-row", methods=["POST"])
def api_admin_db_update_row():
	user, err = _require_admin()
	if err:
		return err

	data = flask.request.json or {}
	table = str(data.get("table", "")).strip()
	schema = str(data.get("schema", "public")).strip() or "public"
	if not table:
		return flask.jsonify({"ok": False, "message": "Missing table."}), 400

	try:
		columns, pk_cols = _get_table_meta(schema, table)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": str(e)}), 400

	if not pk_cols:
		return flask.jsonify({"ok": False, "message": "Table has no primary key."}), 400

	updates = {}
	for key, value in data.items():
		if not key.startswith("col__"):
			continue
		col = key[5:]
		if col in columns:
			updates[col] = _parse_db_value(value)

	if not updates:
		return flask.jsonify({"ok": False, "message": "No fields to update."}), 400

	equalities = {}
	for col in pk_cols:
		pk_val = data.get(f"pk__{col}")
		if pk_val is None:
			return flask.jsonify({"ok": False, "message": f"Missing primary key value: {col}."}), 400
		equalities[col] = _parse_db_value(pk_val)

	try:
		updated = interface.client.update_rows_with_equalities(f"{schema}.{table}", updates, equalities)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Update failed: {e}"}), 400

	if updated == 0:
		return flask.jsonify({"ok": False, "message": "No rows updated."}), 404

	logger.info(
		"Admin DB update by user_id=%s table=%s.%s equalities=%s updates=%s",
		user.get("id"),
		schema,
		table,
		equalities,
		updates,
	)
	return flask.jsonify({"ok": True, "message": "Row updated."})


@api.route("/api/admin/db/delete-row", methods=["POST"])
def api_admin_db_delete_row():
	user, err = _require_admin()
	if err:
		return err

	data = flask.request.json or {}
	table = str(data.get("table", "")).strip()
	schema = str(data.get("schema", "public")).strip() or "public"
	if not table:
		return flask.jsonify({"ok": False, "message": "Missing table."}), 400

	try:
		columns, pk_cols = _get_table_meta(schema, table)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": str(e)}), 400

	if not pk_cols:
		return flask.jsonify({"ok": False, "message": "Table has no primary key."}), 400

	equalities = {}
	for col in pk_cols:
		pk_val = data.get(f"pk__{col}")
		if pk_val is None:
			return flask.jsonify({"ok": False, "message": f"Missing primary key value: {col}."}), 400
		equalities[col] = _parse_db_value(pk_val)

	try:
		deleted = interface.client.delete_rows_with_filters(f"{schema}.{table}", equalities=equalities)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Delete failed: {e}"}), 400

	if deleted == 0:
		return flask.jsonify({"ok": False, "message": "No rows deleted."}), 404

	logger.info(
		"Admin DB delete by user_id=%s table=%s.%s equalities=%s",
		user.get("id"),
		schema,
		table,
		equalities,
	)
	if schema == "public" and table == "admins":
		_notify_moderators(
			"role_revoked",
			title="Admin role revoked",
			actor=user.get("email") or user.get("id"),
			subject=equalities.get("user_id"),
			details=[
				f"User ID: {equalities.get('user_id')}",
			],
			context={
				"action": "role_revoked",
				"role": "admin",
				"user_id": equalities.get("user_id"),
				"reviewer_user_id": user.get("id"),
			},
		)
	return flask.jsonify({"ok": True, "message": "Row deleted."})


@api.route("/api/admin/db/insert-row", methods=["POST"])
def api_admin_db_insert_row():
	user, err = _require_admin()
	if err:
		return err

	data = flask.request.json or {}
	table = str(data.get("table", "")).strip()
	schema = str(data.get("schema", "public")).strip() or "public"
	if not table:
		return flask.jsonify({"ok": False, "message": "Missing table."}), 400

	try:
		columns, _ = _get_table_meta(schema, table)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": str(e)}), 400

	inserts = {}
	for key, value in data.items():
		if not key.startswith("col__"):
			continue
		col = key[5:]
		if col not in columns:
			continue
		val = _parse_db_value(value)
		if val is None:
			continue
		inserts[col] = val

	if not inserts:
		return flask.jsonify({"ok": False, "message": "No fields to insert."}), 400

	try:
		row = interface.client.insert_row(f"{schema}.{table}", inserts)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Insert failed: {e}"}), 400

	logger.info(
		"Admin DB insert by user_id=%s table=%s.%s inserts=%s",
		user.get("id"),
		schema,
		table,
		inserts,
	)
	if schema == "public" and table == "admins":
		_notify_moderators(
			"role_granted",
			title="Admin role granted",
			actor=user.get("email") or user.get("id"),
			subject=inserts.get("user_id"),
			details=[
				f"User ID: {inserts.get('user_id')}",
			],
			context={
				"action": "role_granted",
				"role": "admin",
				"user_id": inserts.get("user_id"),
				"reviewer_user_id": user.get("id"),
			},
		)
	return flask.jsonify({"ok": True, "message": "Row inserted.", "row": row})

@api.route("/delete-account", methods=["POST"])
def api_delete_account():
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if not token:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401
	user = UserManagement.get_user_by_session_token(token)
	if not user:
		return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401

	data = flask.request.json or {}
	password = (data.get("password") or "").strip()
	if not password:
		return flask.jsonify({"ok": False, "message": "Password is required."}), 400

	try:
		stored_hash = user.get("password_hash")
		if not stored_hash:
			return flask.jsonify({"ok": False, "message": "Password not set for this account."}), 400
		if not bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
			return flask.jsonify({"ok": False, "message": "Incorrect password."}), 401

		interface.client.update_rows_with_filters(
			"users",
			{"is_active": False},
			raw_conditions=["id = %s"],
			raw_params=[user.get("id")],
		)
		interface.client.update_rows_with_filters(
			"user_sessions",
			{"revoked_at": datetime.now(timezone.utc)},
			raw_conditions=["user_id = %s", "revoked_at IS NULL"],
			raw_params=[user.get("id")],
		)
		interface.client.delete_rows_with_filters(
			"discord_webhooks",
			raw_conditions=["user_id = %s"],
			raw_params=[user.get("id")],
		)
		interface.client.delete_rows_with_filters(
			"minecraft_whitelist",
			raw_conditions=["user_id = %s"],
			raw_params=[user.get("id")],
		)
		interface.client.delete_rows_with_filters(
			"audiobookshelf_registrations",
			raw_conditions=["user_id = %s"],
			raw_params=[user.get("id")],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Failed to delete account: {e}"}), 400

	resp = flask.make_response(flask.jsonify({"ok": True, "message": "Account deleted."}))
	resp.set_cookie(
		key=_AUTH_TOKEN_NAME_,
		value="",
		httponly=True,
		secure=True,
		samesite="Lax",
		max_age=0,
		path="/",
	)
	return resp

@api.route("/audiobookshelf-registration", methods=["POST"])
def api_audiobookshelf_registration():
	data = flask.request.json or {}
	first_name = (data.get("first_name") or "").strip()
	last_name = (data.get("last_name") or "").strip()
	email = (data.get("email") or "").strip().lower()
	additional_info = (data.get("additional_info") or "").strip()
	user_id = None
	user = None
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if token:
		user = UserManagement.get_user_by_session_token(token)
	if user:
		user_id = user.get("id")
	is_admin = False
	if user_id:
		try:
			is_admin = interface.is_admin(user_id)
		except Exception:
			is_admin = False

	if not first_name or not last_name or not email:
		if user:
			first_name = first_name or user.get("first_name", "")
			last_name = last_name or user.get("last_name", "")
			email = email or (user.get("email", "") or "").lower()
		if not first_name or not last_name or not email:
			return flask.jsonify({
				"ok": False,
				"message": "First name, last name, and email are required.",
			}), 400
	if not user:
		ok, anon_id = _get_or_create_anonymous_user(
			first_name=first_name,
			last_name=last_name,
			email=email,
		)
		if not ok:
			return flask.jsonify({"ok": False, "message": anon_id}), 400
		user_id = anon_id

	if "@" not in email:
		return flask.jsonify({
			"ok": False,
			"message": "Invalid email address.",
		}), 400

	try:
		row = interface.client.insert_row("audiobookshelf_registrations", {
			"first_name": first_name,
			"last_name": last_name,
			"user_id": user_id,
			"email": email,
			"additional_info": additional_info or None,
			"status": "approved" if is_admin else "pending",
			"is_active": True if is_admin else True,
			"reviewed_at": datetime.now(timezone.utc) if is_admin else None,
			"reviewed_by_user_id": user_id if is_admin else None,
		})
		if user_id:
			interface.client.delete_rows_with_filters(
				"application_exemptions",
				raw_conditions=["user_id = %s", "integration_type = 'audiobookshelf'"],
				raw_params=[user_id],
			)
		if is_admin:
			_notify_moderators(
				"audiobookshelf_request_approved",
				title="Audiobookshelf request auto-approved",
				actor=user.get("email") if user else "Admin",
				subject=f"{first_name} {last_name}".strip() or email,
				details=[
					f"Email: {email}" if email else "",
					f"User ID: {user_id}" if user_id else "User ID: anonymous",
					f"Additional info: {additional_info}" if additional_info else "",
					"Auto-approved (admin request).",
					f"Request ID: {row['id']}",
				],
				context={
					"action": "audiobookshelf_request_approved",
					"user_id": user_id,
					"email": email,
					"reviewer_user_id": user_id,
					"request_id": str(row["id"]),
				},
			)
		else:
			_notify_moderators(
				"audiobookshelf_request_submitted",
				title="New audiobookshelf request",
				actor=user.get("email") if user else "Anonymous",
				subject=f"{first_name} {last_name}".strip() or email,
				details=[
					f"Email: {email}" if email else "",
					f"User ID: {user_id}" if user_id else "User ID: anonymous",
					f"Additional info: {additional_info}" if additional_info else "",
				],
				buttons=_build_admin_action_buttons("audiobookshelf", str(row["id"])),
				context={
					"action": "audiobookshelf_request_submitted",
					"user_id": user_id,
					"email": email,
				},
			)
	except Exception as e:
		return flask.jsonify({
			"ok": False,
			"message": f"Failed to submit registration: {e}",
		}), 400

	return flask.jsonify({
		"ok": True,
		"message": "Registration approved." if is_admin else "Registration submitted. You will receive a follow-up email if approved.",
	})


@api.route("/minecraft-registration", methods=["POST"])
def api_minecraft_registration():
	data = flask.request.json or {}
	first_name = (data.get("first_name") or "").strip()
	last_name = (data.get("last_name") or "").strip()
	email = (data.get("email") or "").strip().lower()
	mc_username = (data.get("mc_username") or "").strip()
	who_are_you = (data.get("who_are_you") or "").strip()
	additional_info = (data.get("additional_info") or "").strip()
	user_id = None
	user = None
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if token:
		user = UserManagement.get_user_by_session_token(token)
	if user:
		user_id = user.get("id")
	is_admin = False
	if user_id:
		try:
			is_admin = interface.is_admin(user_id)
		except Exception:
			is_admin = False

	if not first_name or not last_name or not email:
		if user:
			first_name = first_name or user.get("first_name", "")
			last_name = last_name or user.get("last_name", "")
			email = email or (user.get("email", "") or "").lower()
		if not first_name or not last_name or not email:
			return flask.jsonify({
				"ok": False,
				"message": "First name, last name, and email are required.",
			}), 400
	if not user:
		ok, anon_id = _get_or_create_anonymous_user(
			first_name=first_name,
			last_name=last_name,
			email=email,
		)
		if not ok:
			return flask.jsonify({"ok": False, "message": anon_id}), 400
		user_id = anon_id

	if "@" not in email:
		return flask.jsonify({
			"ok": False,
			"message": "Invalid email address.",
		}), 400

	if not mc_username:
		return flask.jsonify({
			"ok": False,
			"message": "Minecraft username is required.",
		}), 400

	if not who_are_you:
		return flask.jsonify({
			"ok": False,
			"message": "Please select who you are.",
		}), 400

	try:
		exemption = None
		if user_id:
			exemption = interface.client.execute_query(
				"SELECT id FROM application_exemptions "
				"WHERE user_id = %s AND integration_type = 'minecraft' AND integration_key = %s LIMIT 1;",
				(user_id, mc_username),
			) or []
		existing = interface.client.execute_query(
			"SELECT 1 FROM minecraft_registrations WHERE LOWER(mc_username) = LOWER(%s) LIMIT 1;",
			(mc_username,),
		) or []
		if existing and not exemption:
			return flask.jsonify({
				"ok": False,
				"message": "That Minecraft username already has an application on file.",
			}), 400
		if exemption:
			interface.client.delete_rows_with_filters(
				"application_exemptions",
				raw_conditions=["id = %s"],
				raw_params=[exemption[0]["id"]],
			)
			interface.client.delete_rows_with_filters(
				"minecraft_registrations",
				raw_conditions=["LOWER(mc_username) = LOWER(%s)"],
				raw_params=[mc_username],
			)
		whitelisted = interface.client.execute_query(
			"SELECT 1 FROM minecraft_whitelist WHERE LOWER(mc_username) = LOWER(%s) AND is_active = TRUE LIMIT 1;",
			(mc_username,),
		) or []
		if whitelisted:
			return flask.jsonify({
				"ok": False,
				"message": "That Minecraft username is already whitelisted.",
			}), 400
		row = interface.client.insert_row("minecraft_registrations", {
			"first_name": first_name,
			"user_id": user_id,
			"last_name": last_name,
			"email": email,
			"mc_username": mc_username,
			"who_are_you": who_are_you,
			"additional_info": additional_info or None,
			"status": "approved" if is_admin else "pending",
			"reviewed_at": datetime.now(timezone.utc) if is_admin else None,
			"reviewed_by_user_id": user_id if is_admin else None,
		})
		if is_admin:
			existing = interface.client.execute_query(
				"SELECT id, is_active FROM minecraft_whitelist WHERE LOWER(mc_username) = LOWER(%s) LIMIT 1;",
				(mc_username,),
			) or []
			if not existing:
				interface.client.insert_row("minecraft_whitelist", {
					"user_id": user_id,
					"first_name": first_name,
					"last_name": last_name,
					"email": email,
					"mc_username": mc_username,
					"is_active": True,
				})
			else:
				interface.client.update_rows_with_filters(
					"minecraft_whitelist",
					{"is_active": True, "ban_reason": None},
					raw_conditions=["id = %s"],
					raw_params=[existing[0]["id"]],
				)
			_notify_moderators(
				"minecraft_request_approved",
				title="Minecraft request auto-approved",
				actor=user.get("email") if user else "Admin",
				subject=mc_username,
				details=[
					f"Name: {first_name} {last_name}".strip(),
					f"Email: {email}" if email else "",
					f"User ID: {user_id}" if user_id else "User ID: anonymous",
					f"Who are you: {who_are_you}" if who_are_you else "",
					f"Additional info: {additional_info}" if additional_info else "",
					"Auto-approved (admin request).",
					f"Request ID: {row['id']}",
				],
				context={
					"action": "minecraft_request_approved",
					"user_id": user_id,
					"email": email,
					"mc_username": mc_username,
					"reviewer_user_id": user_id,
					"request_id": str(row["id"]),
				},
			)
		else:
			_notify_moderators(
				"minecraft_request_submitted",
				title="New Minecraft whitelist request",
				actor=user.get("email") if user else "Anonymous",
				subject=mc_username,
				details=[
					f"Name: {first_name} {last_name}".strip(),
					f"Email: {email}" if email else "",
					f"User ID: {user_id}" if user_id else "User ID: anonymous",
					f"Who are you: {who_are_you}" if who_are_you else "",
					f"Additional info: {additional_info}" if additional_info else "",
				],
				buttons=_build_admin_action_buttons("minecraft", str(row["id"])),
				context={
					"action": "minecraft_request_submitted",
					"user_id": user_id,
					"email": email,
					"mc_username": mc_username,
				},
			)
	except Exception as e:
		return flask.jsonify({
			"ok": False,
			"message": f"Failed to submit registration: {e}",
		}), 400

	return flask.jsonify({
		"ok": True,
		"message": "Request approved." if is_admin else "Request submitted. You will receive a follow-up email if approved.",
	})


@api.route("/api/minecraft/status")
def api_minecraft_status():
	host = "mc.zubekanov.com"
	port = 25565
	cache_ttl = 300

	def _fetch_status() -> dict[str, object]:
		start = time.time()
		try:
			from mcstatus import JavaServer
		except Exception as e:
			return {
				"ok": False,
				"error": f"mcstatus not available: {e}",
				"host": host,
				"port": port,
			}

		try:
			server = JavaServer.lookup(f"{host}:{port}")
			status = server.status()
			latency_ms = int(round((time.time() - start) * 1000))

			# Description can be str or dict depending on mcstatus version
			desc = status.description
			if isinstance(desc, dict):
				motd = desc.get("text") or ""
			else:
				motd = str(desc) if desc is not None else ""

			return {
				"ok": True,
				"online": True,
				"host": host,
				"port": port,
				"motd": motd,
				"players_online": getattr(status.players, "online", None),
				"players_max": getattr(status.players, "max", None),
				"version": getattr(status.version, "name", None),
				"latency_ms": latency_ms,
			}
		except Exception as e:
			return {
				"ok": True,
				"online": False,
				"host": host,
				"port": port,
				"error": str(e),
			}

	def _refresh_status_async():
		data = _fetch_status()
		fetched_at = datetime.now(timezone.utc)
		with _MINECRAFT_STATUS_LOCK:
			_MINECRAFT_STATUS_CACHE["data"] = data
			_MINECRAFT_STATUS_CACHE["fetched_at_ts"] = fetched_at.timestamp()
			_MINECRAFT_STATUS_CACHE["fetched_at_iso"] = fetched_at.isoformat()
			_MINECRAFT_STATUS_CACHE["refreshing"] = False

	now = time.time()
	with _MINECRAFT_STATUS_LOCK:
		cached = _MINECRAFT_STATUS_CACHE.get("data")
		fetched_at_ts = _MINECRAFT_STATUS_CACHE.get("fetched_at_ts")
		refreshing = bool(_MINECRAFT_STATUS_CACHE.get("refreshing"))

	if cached and fetched_at_ts:
		age = now - float(fetched_at_ts)
		if age < cache_ttl:
			response = dict(cached)
			response["cached"] = True
			response["refreshing"] = False
			response["age_seconds"] = int(age)
			response["fetched_at"] = _MINECRAFT_STATUS_CACHE.get("fetched_at_iso")
			return flask.jsonify(response)

		if not refreshing:
			with _MINECRAFT_STATUS_LOCK:
				_MINECRAFT_STATUS_CACHE["refreshing"] = True
			threading.Thread(target=_refresh_status_async, daemon=True).start()

		response = dict(cached)
		response["cached"] = True
		response["refreshing"] = True
		response["age_seconds"] = int(age)
		response["fetched_at"] = _MINECRAFT_STATUS_CACHE.get("fetched_at_iso")
		return flask.jsonify(response)

	data = _fetch_status()
	fetched_at = datetime.now(timezone.utc)
	with _MINECRAFT_STATUS_LOCK:
		_MINECRAFT_STATUS_CACHE["data"] = data
		_MINECRAFT_STATUS_CACHE["fetched_at_ts"] = fetched_at.timestamp()
		_MINECRAFT_STATUS_CACHE["fetched_at_iso"] = fetched_at.isoformat()
		_MINECRAFT_STATUS_CACHE["refreshing"] = False
	response = dict(data)
	response["cached"] = False
	response["refreshing"] = False
	response["age_seconds"] = 0
	response["fetched_at"] = _MINECRAFT_STATUS_CACHE.get("fetched_at_iso")
	return flask.jsonify(response)


@api.route("/discord-webhook-registration", methods=["POST"])
def api_discord_webhook_registration():
	return flask.jsonify({
		"ok": False,
		"message": "Use the verification flow to submit webhooks.",
	}), 400


@api.route("/discord-webhook/verify", methods=["POST"])
def api_discord_webhook_verify():
	data = flask.request.json or {}
	name = (data.get("name") or "").strip()
	webhook_url = (data.get("webhook_url") or "").strip()
	event_key = (data.get("event_key") or "").strip()
	first_name = (data.get("first_name") or "").strip()
	last_name = (data.get("last_name") or "").strip()
	contact_email = (data.get("contact_email") or "").strip()

	if not name or not webhook_url or not event_key:
		return flask.jsonify({
			"ok": False,
			"message": "Name, webhook URL, and event key are required.",
		}), 400

	user = None
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if token:
		user = UserManagement.get_user_by_session_token(token)
	is_admin = bool(user and interface.is_admin(user.get("id")))

	allowed_permissions = ["all"]
	if user:
		allowed_permissions.append("users")
	if is_admin:
		allowed_permissions.append("admins")

	rows, _ = interface.client.get_rows_with_filters(
		"discord_event_keys",
		raw_conditions=["permission = ANY(%s)"],
		raw_params=[allowed_permissions],
		page_limit=1000,
		page_num=0,
	)
	allowed = {r["event_key"] for r in rows} if rows else set()
	if allowed and event_key not in allowed:
		return flask.jsonify({
			"ok": False,
			"message": "Event key is not permitted for this user.",
		}), 403
	try:
		exemption = None
		if user_id:
			exemption = interface.client.execute_query(
				"SELECT id FROM application_exemptions "
				"WHERE user_id = %s AND integration_type = 'discord_webhook' LIMIT 1;",
				(user_id,),
			) or []
		existing_sub = interface.client.execute_query(
			"SELECT s.id, s.is_active, w.is_active AS webhook_active "
			"FROM discord_webhook_subscriptions s "
			"JOIN discord_webhooks w ON w.id = s.webhook_id "
			"WHERE w.webhook_url = %s AND s.event_key = %s LIMIT 1;",
			(webhook_url, event_key),
		) or []
		if existing_sub:
			sub = existing_sub[0]
			if not bool(sub.get("webhook_active", True)):
				return flask.jsonify({
					"ok": False,
					"message": "That webhook is inactive and cannot be re-verified. Contact an admin.",
				}), 403
			if not bool(sub.get("is_active", True)):
				interface.client.update_rows_with_filters(
					"discord_webhook_subscriptions",
					{"is_active": True},
					raw_conditions=["id = %s"],
					raw_params=[sub["id"]],
				)
				return flask.jsonify({
					"ok": True,
					"message": "Subscription reactivated.",
					"redirect": "/discord-webhook/verified?status=reactivated",
				})
			if exemption:
				interface.client.delete_rows_with_filters(
					"application_exemptions",
					raw_conditions=["id = %s"],
					raw_params=[exemption[0]["id"]],
				)
				interface.client.delete_rows_with_filters(
					"discord_webhook_registrations",
					raw_conditions=["webhook_url = %s", "event_key = %s"],
					raw_params=[webhook_url, event_key],
				)
			else:
				return flask.jsonify({
					"ok": False,
					"message": "That webhook is already subscribed to this event key.",
				}), 400

		existing_webhooks = interface.client.execute_query(
			"SELECT id, is_active FROM discord_webhooks WHERE webhook_url = %s LIMIT 1;",
			(webhook_url,),
		) or []
		if existing_webhooks:
			active = bool(existing_webhooks[0].get("is_active", True))
			if not active:
				return flask.jsonify({
					"ok": False,
					"message": "That webhook is inactive and cannot be re-verified. Contact an admin.",
				}), 403
	except Exception as e:
		return flask.jsonify({
			"ok": False,
			"message": f"Failed to validate existing webhook: {e}",
		}), 400
	try:
		existing = interface.client.execute_query(
			"SELECT 1 FROM discord_webhook_registrations WHERE webhook_url = %s AND event_key = %s LIMIT 1;",
			(webhook_url, event_key),
		) or []
		if existing and not exemption:
			return flask.jsonify({
				"ok": False,
				"message": "That webhook URL is already registered for this event key.",
			}), 400
	except Exception as e:
		return flask.jsonify({
			"ok": False,
			"message": f"Failed to validate webhook uniqueness: {e}",
		}), 400

	if existing_webhooks:
		user_id = user.get("id") if user else None
		if not user_id:
			if not first_name or not last_name or not contact_email:
				return flask.jsonify({
					"ok": False,
					"message": "First name, last name, and contact email are required.",
				}), 400
		try:
			row = interface.client.insert_row("discord_webhook_registrations", {
				"name": name,
				"webhook_url": webhook_url,
				"event_key": event_key,
				"submitted_by_user_id": user_id,
				"submitted_by_name": f"{first_name} {last_name}".strip() if not user_id else None,
				"submitted_by_email": contact_email if not user_id else None,
				"status": "pending",
			})
			if is_admin:
				emitter = DiscordWebhookEmitter(interface)
				ok, msg = emitter.approve_registration(
					registration_id=str(row["id"]),
					reviewer_user_id=user_id,
				)
				if not ok:
					return flask.jsonify({"ok": False, "message": msg}), 400
				_notify_moderators(
					"discord_webhook_request_approved",
					title="Discord webhook auto-approved",
					actor=user.get("email") if user else "Admin",
					subject=name or webhook_url,
					details=[
						f"Event key: {event_key}",
						f"Webhook URL: {webhook_url}",
						f"User ID: {user_id}" if user_id else "User ID: anonymous",
						"Auto-approved (admin request).",
						f"Request ID: {row['id']}",
					],
					context={
						"action": "discord_webhook_request_approved",
						"user_id": user_id,
						"event_key": event_key,
						"reviewer_user_id": user_id,
						"request_id": str(row["id"]),
					},
				)
			else:
				_notify_moderators(
					"discord_webhook_request_submitted",
					title="New Discord webhook request",
					actor=user.get("email") if user else "Anonymous",
					subject=name or webhook_url,
					details=[
						f"Event key: {event_key}",
						f"Webhook URL: {webhook_url}",
						f"User ID: {user_id}" if user_id else "User ID: anonymous",
						f"Contact: {first_name} {last_name} <{contact_email}>"
						if not user_id and (first_name or last_name or contact_email)
						else "",
					],
					buttons=_build_admin_action_buttons("discord-webhook", str(row["id"])),
					context={
						"action": "discord_webhook_request_submitted",
						"user_id": user_id,
						"event_key": event_key,
					},
				)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": f"Failed to submit: {e}"}), 400

		return flask.jsonify({
			"ok": True,
			"message": "Request approved." if is_admin else "Request submitted.",
			"redirect": "/discord-webhook/verified?status=approved" if is_admin else "/discord-webhook/verified?status=submitted",
		})

	code = f"{secrets.randbelow(1000000):06d}"
	verify_id = None
	# Store verification first to include a link in the message.
	secret = interface._token_secret()
	code_hash = hmac.new(secret, code.encode("utf-8"), hashlib.sha256).hexdigest()
	expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
	user_id = user.get("id") if user else None
	if not user_id:
		if not first_name or not last_name or not contact_email:
			return flask.jsonify({
				"ok": False,
				"message": "First name, last name, and email are required when not logged in.",
			}), 400
		if "@" not in contact_email:
			return flask.jsonify({
				"ok": False,
				"message": "Invalid contact email address.",
			}), 400
		ok, anon_id = _get_or_create_anonymous_user(
			first_name=first_name,
			last_name=last_name,
			email=contact_email,
		)
		if not ok:
			return flask.jsonify({"ok": False, "message": anon_id}), 400
		user_id = anon_id

	try:
		interface.client.delete_rows_with_filters(
			"discord_webhook_verifications",
			raw_conditions=["webhook_url = %s", "event_key = %s"],
			raw_params=[webhook_url, event_key],
		)
		row = interface.client.insert_row("discord_webhook_verifications", {
			"webhook_url": webhook_url,
			"event_key": event_key,
			"name": name,
			"code_hash": code_hash,
			"expires_at": expires_at,
			"requested_by_user_id": user_id,
			"contact_name": f"{first_name} {last_name}".strip() or None,
			"contact_email": contact_email or None,
		})
		verify_id = str(row["id"])
	except Exception as e:
		return flask.jsonify({
			"ok": False,
			"message": f"Failed to store verification: {e}",
		}), 400

	verify_link = f"{flask.request.host_url.rstrip('/')}/token?vid={verify_id}&code={code}"
	payload = {
		"content": (
			f"Webhook verification code: {code}\n"
			f"This verifies the webhook for event key: {event_key}\n"
			f"Submit code: {verify_link}"
		)
	}

	emitter = DiscordWebhookEmitter(interface)
	res = emitter.send_test_message(webhook_url, payload)
	if not res.ok:
		return flask.jsonify({
			"ok": False,
			"message": f"Webhook verification failed: {res.error or res.status_code}",
		}), 400

	return flask.jsonify({
		"ok": True,
		"message": "Verification code sent. Check your Discord channel.",
		"redirect": f"/token?vid={verify_id}",
	})


@api.route("/discord-webhook/verify/submit", methods=["POST"])
def api_discord_webhook_verify_submit():
	data = flask.request.json or {}
	verify_id = (data.get("verification_id") or "").strip()
	code = (data.get("verification_code") or "").strip()
	if not verify_id or not code:
		return flask.jsonify({"ok": False, "message": "Missing verification data."}), 400

	rows, _ = interface.client.get_rows_with_filters(
		"discord_webhook_verifications",
		raw_conditions=["id = %s", "expires_at >= NOW()"],
		raw_params=[verify_id],
		page_limit=1,
		page_num=0,
	)
	if not rows:
		return flask.jsonify({"ok": False, "message": "Verification expired or invalid."}), 400
	ver = rows[0]

	secret = interface._token_secret()
	code_hash = hmac.new(secret, code.encode("utf-8"), hashlib.sha256).hexdigest()
	if code_hash != ver.get("code_hash"):
		return flask.jsonify({"ok": False, "message": "Invalid verification code."}), 400

	user = None
	token = flask.request.cookies.get(_AUTH_TOKEN_NAME_)
	if token:
		user = UserManagement.get_user_by_session_token(token)
	user_id = ver.get("requested_by_user_id") or (user.get("id") if user else None)
	is_admin = False
	if user_id:
		try:
			is_admin = interface.is_admin(user_id)
		except Exception:
			is_admin = False
	try:
		exemption = None
		if user_id:
			exemption = interface.client.execute_query(
				"SELECT id FROM application_exemptions "
				"WHERE user_id = %s AND integration_type = 'discord_webhook' LIMIT 1;",
				(user_id,),
			) or []
			if exemption:
				interface.client.delete_rows_with_filters(
					"application_exemptions",
					raw_conditions=["id = %s"],
					raw_params=[exemption[0]["id"]],
				)
				interface.client.delete_rows_with_filters(
					"discord_webhook_registrations",
					raw_conditions=["webhook_url = %s", "event_key = %s"],
					raw_params=[ver["webhook_url"], ver["event_key"]],
				)
		existing_sub = interface.client.execute_query(
			"SELECT s.id, s.is_active, w.is_active AS webhook_active "
			"FROM discord_webhook_subscriptions s "
			"JOIN discord_webhooks w ON w.id = s.webhook_id "
			"WHERE w.webhook_url = %s AND s.event_key = %s LIMIT 1;",
			(ver["webhook_url"], ver["event_key"]),
		) or []
		if existing_sub:
			sub = existing_sub[0]
			if not bool(sub.get("webhook_active", True)):
				return flask.jsonify({
					"ok": False,
					"message": "That webhook is inactive and cannot be re-verified. Contact an admin.",
				}), 403
			if not bool(sub.get("is_active", True)):
				interface.client.update_rows_with_filters(
					"discord_webhook_subscriptions",
					{"is_active": True},
					raw_conditions=["id = %s"],
					raw_params=[sub["id"]],
				)
				interface.client.delete_rows_with_filters(
					"discord_webhook_verifications",
					raw_conditions=["id = %s"],
					raw_params=[verify_id],
				)
				return flask.jsonify({
					"ok": True,
					"message": "Subscription reactivated.",
					"redirect": "/discord-webhook/verified?status=reactivated",
				})
			if not exemption:
				return flask.jsonify({
					"ok": False,
					"message": "That webhook is already subscribed to this event key.",
				}), 400

		existing_webhooks = interface.client.execute_query(
			"SELECT id, is_active FROM discord_webhooks WHERE webhook_url = %s LIMIT 1;",
			(ver["webhook_url"],),
		) or []
		if existing_webhooks and not bool(existing_webhooks[0].get("is_active", True)):
			return flask.jsonify({
				"ok": False,
				"message": "That webhook is inactive and cannot be re-verified. Contact an admin.",
			}), 403

		existing = interface.client.execute_query(
			"SELECT 1 FROM discord_webhook_registrations WHERE webhook_url = %s AND event_key = %s LIMIT 1;",
			(ver["webhook_url"], ver["event_key"]),
		) or []
		if existing and not exemption:
			return flask.jsonify({
				"ok": False,
				"message": "That webhook URL is already registered for this event key.",
			}), 400
		row = interface.client.insert_row("discord_webhook_registrations", {
			"name": ver["name"],
			"webhook_url": ver["webhook_url"],
			"event_key": ver["event_key"],
			"submitted_by_user_id": user_id,
			"submitted_by_name": ver.get("contact_name"),
			"submitted_by_email": ver.get("contact_email"),
			"status": "pending",
		})
		if is_admin:
			emitter = DiscordWebhookEmitter(interface)
			ok, msg = emitter.approve_registration(
				registration_id=str(row["id"]),
				reviewer_user_id=user_id,
			)
			if not ok:
				return flask.jsonify({"ok": False, "message": msg}), 400
			_notify_moderators(
				"discord_webhook_request_approved",
				title="Discord webhook auto-approved",
				actor=user.get("email") if user else "Admin",
				subject=ver.get("name") or ver.get("webhook_url"),
				details=[
					f"Event key: {ver.get('event_key')}",
					f"Webhook URL: {ver.get('webhook_url')}",
					f"User ID: {user_id}" if user_id else "User ID: anonymous",
					f"Contact: {ver.get('contact_name')} <{ver.get('contact_email')}>" if ver.get("contact_name") or ver.get("contact_email") else "",
					"Auto-approved (admin request).",
					f"Request ID: {row['id']}",
				],
				context={
					"action": "discord_webhook_request_approved",
					"user_id": user_id,
					"event_key": ver.get("event_key"),
					"reviewer_user_id": user_id,
					"request_id": str(row["id"]),
				},
			)
		else:
			_notify_moderators(
				"discord_webhook_request_submitted",
				title="New Discord webhook request",
				actor=user.get("email") if user else "Anonymous",
				subject=ver.get("name") or ver.get("webhook_url"),
				details=[
					f"Event key: {ver.get('event_key')}",
					f"Webhook URL: {ver.get('webhook_url')}",
					f"User ID: {user_id}" if user_id else "User ID: anonymous",
					f"Contact: {ver.get('contact_name')} <{ver.get('contact_email')}>" if ver.get("contact_name") or ver.get("contact_email") else "",
				],
				buttons=_build_admin_action_buttons("discord-webhook", str(row["id"])),
				context={
					"action": "discord_webhook_request_submitted",
					"user_id": user_id,
					"event_key": ver.get("event_key"),
				},
			)
		interface.client.delete_rows_with_filters(
			"discord_webhook_verifications",
			raw_conditions=["id = %s"],
			raw_params=[verify_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Failed to submit: {e}"}), 400

	return flask.jsonify({
		"ok": True,
		"redirect": "/discord-webhook/verified?status=approved" if is_admin else "/discord-webhook/verified?status=submitted",
		"message": "Request approved." if is_admin else "Request submitted.",
	})


@api.route("/api/admin/audiobookshelf/approve", methods=["POST"])
def api_admin_audiobookshelf_approve():
	user, err = _require_admin()
	if err:
		return err
	data = flask.request.json or {}
	reg_id = (data.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		rows = interface.client.execute_query(
			"SELECT first_name, last_name, email FROM audiobookshelf_registrations WHERE id = %s;",
			(reg_id,),
		) or []
		if not rows:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		reg = rows[0]
		updated = interface.client.update_rows_with_filters(
			"audiobookshelf_registrations",
			{
				"status": "approved",
				"is_active": True,
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": user.get("id"),
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Approve failed: {e}"}), 400
	if updated == 0:
		return flask.jsonify({"ok": False, "message": "Not found."}), 404
	_notify_moderators(
		"audiobookshelf_request_approved",
		title="Audiobookshelf request approved",
		actor=user.get("email") or user.get("id"),
		subject=f"{reg.get('first_name', '')} {reg.get('last_name', '')}".strip() or reg.get("email"),
		details=[
			f"Email: {reg.get('email', '')}",
			f"Request ID: {reg_id}",
		],
		context={
			"action": "audiobookshelf_request_approved",
			"reviewer_user_id": user.get("id"),
			"request_id": reg_id,
		},
	)
	return flask.jsonify({"ok": True, "message": "Approved."})


@api.route("/api/admin/audiobookshelf/approve-link")
def api_admin_audiobookshelf_approve_link():
	user, err = _require_admin()
	if err:
		return err
	reg_id = (flask.request.args.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		interface.client.update_rows_with_filters(
			"audiobookshelf_registrations",
			{
				"status": "approved",
				"is_active": True,
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": user.get("id"),
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Approve failed: {e}"}), 400
	return flask.redirect("/admin/audiobookshelf-approvals")


@api.route("/api/admin/audiobookshelf/deny", methods=["POST"])
def api_admin_audiobookshelf_deny():
	user, err = _require_admin()
	if err:
		return err
	data = flask.request.json or {}
	reg_id = (data.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		rows = interface.client.execute_query(
			"SELECT first_name, last_name, email FROM audiobookshelf_registrations WHERE id = %s;",
			(reg_id,),
		) or []
		if not rows:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		reg = rows[0]
		updated = interface.client.update_rows_with_filters(
			"audiobookshelf_registrations",
			{
				"status": "denied",
				"is_active": False,
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": user.get("id"),
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Deny failed: {e}"}), 400
	if updated == 0:
		return flask.jsonify({"ok": False, "message": "Not found."}), 404
	_notify_moderators(
		"audiobookshelf_request_denied",
		title="Audiobookshelf request denied",
		actor=user.get("email") or user.get("id"),
		subject=f"{reg.get('first_name', '')} {reg.get('last_name', '')}".strip() or reg.get("email"),
		details=[
			f"Email: {reg.get('email', '')}",
			f"Request ID: {reg_id}",
		],
		context={
			"action": "audiobookshelf_request_denied",
			"reviewer_user_id": user.get("id"),
			"request_id": reg_id,
		},
	)
	return flask.jsonify({"ok": True, "message": "Denied."})


@api.route("/api/admin/audiobookshelf/deny-link")
def api_admin_audiobookshelf_deny_link():
	user, err = _require_admin()
	if err:
		return err
	reg_id = (flask.request.args.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		interface.client.update_rows_with_filters(
			"audiobookshelf_registrations",
			{
				"status": "denied",
				"is_active": False,
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": user.get("id"),
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Deny failed: {e}"}), 400
	return flask.redirect("/admin/audiobookshelf-approvals")


@api.route("/api/admin/discord-webhook/approve", methods=["POST"])
def api_admin_discord_webhook_approve():
	user, err = _require_admin()
	if err:
		return err
	data = flask.request.json or {}
	reg_id = (data.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		rows = interface.client.execute_query(
			"SELECT name, event_key, webhook_url FROM discord_webhook_registrations WHERE id = %s;",
			(reg_id,),
		) or []
		if not rows:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		reg = rows[0]
		emitter = DiscordWebhookEmitter(interface)
		ok, msg = emitter.approve_registration(
			registration_id=reg_id,
			reviewer_user_id=user.get("id"),
		)
		if not ok:
			return flask.jsonify({"ok": False, "message": msg}), 400
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Approve failed: {e}"}), 400

	_notify_moderators(
		"discord_webhook_request_approved",
		title="Discord webhook request approved",
		actor=user.get("email") or user.get("id"),
		subject=reg.get("name") or reg.get("webhook_url"),
		details=[
			f"Event key: {reg.get('event_key')}",
			f"Webhook URL: {reg.get('webhook_url')}",
			f"Request ID: {reg_id}",
		],
		context={
			"action": "discord_webhook_request_approved",
			"reviewer_user_id": user.get("id"),
			"event_key": reg.get("event_key"),
			"request_id": reg_id,
		},
	)
	return flask.jsonify({"ok": True, "message": msg})


@api.route("/api/admin/discord-webhook/approve-link")
def api_admin_discord_webhook_approve_link():
	user, err = _require_admin()
	if err:
		return err
	reg_id = (flask.request.args.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		emitter = DiscordWebhookEmitter(interface)
		ok, msg = emitter.approve_registration(
			registration_id=reg_id,
			reviewer_user_id=user.get("id"),
		)
		if not ok:
			return flask.jsonify({"ok": False, "message": msg}), 400
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Approve failed: {e}"}), 400
	return flask.redirect("/admin/discord-webhook-approvals")


@api.route("/api/admin/discord-webhook/deny", methods=["POST"])
def api_admin_discord_webhook_deny():
	user, err = _require_admin()
	if err:
		return err
	data = flask.request.json or {}
	reg_id = (data.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		rows = interface.client.execute_query(
			"SELECT name, event_key, webhook_url FROM discord_webhook_registrations WHERE id = %s;",
			(reg_id,),
		) or []
		if not rows:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		reg = rows[0]
		updated = interface.client.update_rows_with_filters(
			"discord_webhook_registrations",
			{
				"status": "denied",
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": user.get("id"),
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Deny failed: {e}"}), 400
	if updated == 0:
		return flask.jsonify({"ok": False, "message": "Not found."}), 404
	_notify_moderators(
		"discord_webhook_request_denied",
		title="Discord webhook request denied",
		actor=user.get("email") or user.get("id"),
		subject=reg.get("name") or reg.get("webhook_url"),
		details=[
			f"Event key: {reg.get('event_key')}",
			f"Webhook URL: {reg.get('webhook_url')}",
			f"Request ID: {reg_id}",
		],
		context={
			"action": "discord_webhook_request_denied",
			"reviewer_user_id": user.get("id"),
			"event_key": reg.get("event_key"),
			"request_id": reg_id,
		},
	)
	return flask.jsonify({"ok": True, "message": "Denied."})


@api.route("/api/admin/discord-webhook/deny-link")
def api_admin_discord_webhook_deny_link():
	user, err = _require_admin()
	if err:
		return err
	reg_id = (flask.request.args.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		interface.client.update_rows_with_filters(
			"discord_webhook_registrations",
			{
				"status": "denied",
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": user.get("id"),
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Deny failed: {e}"}), 400
	return flask.redirect("/admin/discord-webhook-approvals")


@api.route("/api/admin/audiobookshelf/pending-count")
def api_admin_audiobookshelf_pending_count():
	_, err = _require_admin()
	if err:
		return err
	try:
		count_rows = interface.client.execute_query(
			"SELECT COUNT(*) AS cnt FROM audiobookshelf_registrations WHERE status = 'pending';"
		) or []
		count = int(count_rows[0]["cnt"]) if count_rows else 0
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Failed to fetch count: {e}"}), 400
	return flask.jsonify({"count": count})


@api.route("/api/admin/discord-webhook/pending-count")
def api_admin_discord_webhook_pending_count():
	_, err = _require_admin()
	if err:
		return err
	try:
		count_rows = interface.client.execute_query(
			"SELECT COUNT(*) AS cnt FROM discord_webhook_registrations WHERE status = 'pending';"
		) or []
		count = int(count_rows[0]["cnt"]) if count_rows else 0
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Failed to fetch count: {e}"}), 400
	return flask.jsonify({"count": count})


@api.route("/api/admin/minecraft/approve", methods=["POST"])
def api_admin_minecraft_approve():
	user, err = _require_admin()
	if err:
		return err
	data = flask.request.json or {}
	reg_id = (data.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		rows = interface.client.execute_query(
			"SELECT * FROM minecraft_registrations WHERE id = %s;",
			(reg_id,),
		) or []
		if not rows:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		reg = rows[0]
		existing = interface.client.execute_query(
			"SELECT id FROM minecraft_whitelist WHERE LOWER(mc_username) = LOWER(%s) LIMIT 1;",
			(reg.get("mc_username"),),
		) or []
		if not existing:
			interface.client.insert_row("minecraft_whitelist", {
				"user_id": reg.get("user_id"),
				"first_name": reg.get("first_name"),
				"last_name": reg.get("last_name"),
				"email": reg.get("email"),
				"mc_username": reg.get("mc_username"),
				"is_active": True,
			})
		else:
			interface.client.update_rows_with_filters(
				"minecraft_whitelist",
				{"is_active": True, "ban_reason": None},
				raw_conditions=["id = %s"],
				raw_params=[existing[0]["id"]],
			)
		interface.client.update_rows_with_filters(
			"minecraft_registrations",
			{
				"status": "approved",
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": user.get("id"),
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Approve failed: {e}"}), 400
	_notify_moderators(
		"minecraft_request_approved",
		title="Minecraft whitelist request approved",
		actor=user.get("email") or user.get("id"),
		subject=reg.get("mc_username"),
		details=[
			f"Name: {reg.get('first_name', '')} {reg.get('last_name', '')}".strip(),
			f"Email: {reg.get('email', '')}",
			f"User ID: {reg.get('user_id')}" if reg.get("user_id") else "User ID: anonymous",
			f"Request ID: {reg_id}",
		],
		context={
			"action": "minecraft_request_approved",
			"reviewer_user_id": user.get("id"),
			"mc_username": reg.get("mc_username"),
			"request_id": reg_id,
		},
	)
	return flask.jsonify({"ok": True, "message": "Approved."})


@api.route("/api/admin/minecraft/approve-link")
def api_admin_minecraft_approve_link():
	user, err = _require_admin()
	if err:
		return err
	reg_id = (flask.request.args.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		rows = interface.client.execute_query(
			"SELECT * FROM minecraft_registrations WHERE id = %s;",
			(reg_id,),
		) or []
		if not rows:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		reg = rows[0]
		existing = interface.client.execute_query(
			"SELECT id FROM minecraft_whitelist WHERE LOWER(mc_username) = LOWER(%s) LIMIT 1;",
			(reg.get("mc_username"),),
		) or []
		if not existing:
			interface.client.insert_row("minecraft_whitelist", {
				"user_id": reg.get("user_id"),
				"first_name": reg.get("first_name"),
				"last_name": reg.get("last_name"),
				"email": reg.get("email"),
				"mc_username": reg.get("mc_username"),
				"is_active": True,
			})
		else:
			interface.client.update_rows_with_filters(
				"minecraft_whitelist",
				{"is_active": True, "ban_reason": None},
				raw_conditions=["id = %s"],
				raw_params=[existing[0]["id"]],
			)
		interface.client.update_rows_with_filters(
			"minecraft_registrations",
			{
				"status": "approved",
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": user.get("id"),
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Approve failed: {e}"}), 400
	return flask.redirect("/admin/minecraft-approvals")


@api.route("/api/admin/minecraft/deny", methods=["POST"])
def api_admin_minecraft_deny():
	user, err = _require_admin()
	if err:
		return err
	data = flask.request.json or {}
	reg_id = (data.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		rows = interface.client.execute_query(
			"SELECT * FROM minecraft_registrations WHERE id = %s;",
			(reg_id,),
		) or []
		if not rows:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		reg = rows[0]
		updated = interface.client.update_rows_with_filters(
			"minecraft_registrations",
			{
				"status": "denied",
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Deny failed: {e}"}), 400
	if updated == 0:
		return flask.jsonify({"ok": False, "message": "Not found."}), 404
	_notify_moderators(
		"minecraft_request_denied",
		title="Minecraft whitelist request denied",
		actor=user.get("email") or user.get("id"),
		subject=reg.get("mc_username"),
		details=[
			f"Name: {reg.get('first_name', '')} {reg.get('last_name', '')}".strip(),
			f"Email: {reg.get('email', '')}",
			f"User ID: {reg.get('user_id')}" if reg.get("user_id") else "User ID: anonymous",
			f"Request ID: {reg_id}",
		],
		context={
			"action": "minecraft_request_denied",
			"reviewer_user_id": user.get("id"),
			"mc_username": reg.get("mc_username"),
			"request_id": reg_id,
		},
	)
	return flask.jsonify({"ok": True, "message": "Denied."})


@api.route("/api/admin/minecraft/deny-link")
def api_admin_minecraft_deny_link():
	user, err = _require_admin()
	if err:
		return err
	reg_id = (flask.request.args.get("id") or "").strip()
	if not reg_id:
		return flask.jsonify({"ok": False, "message": "Missing id."}), 400
	try:
		interface.client.update_rows_with_filters(
			"minecraft_registrations",
			{
				"status": "denied",
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": user.get("id"),
			},
			raw_conditions=["id = %s"],
			raw_params=[reg_id],
		)
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Deny failed: {e}"}), 400
	return flask.redirect("/admin/minecraft-approvals")


@api.route("/api/discord/interactions", methods=["POST"])
def api_discord_interactions():
	signature = flask.request.headers.get("X-Signature-Ed25519", "")
	timestamp = flask.request.headers.get("X-Signature-Timestamp", "")
	body = flask.request.get_data() or b""

	if not _verify_discord_signature(signature, timestamp, body):
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
			return flask.jsonify(_discord_interaction_response("Unsupported action.", ok=False))
		_, action, kind, reg_id = parts
		ok, message = _handle_mod_action(kind, action, reg_id)
		return flask.jsonify(_discord_interaction_response(message, ok=ok))

	return flask.jsonify(_discord_interaction_response("Unsupported interaction type.", ok=False))


@api.route("/api/admin/minecraft/pending-count")
def api_admin_minecraft_pending_count():
	_, err = _require_admin()
	if err:
		return err
	try:
		count_rows = interface.client.execute_query(
			"SELECT COUNT(*) AS cnt FROM minecraft_registrations WHERE status = 'pending';"
		) or []
		count = int(count_rows[0]["cnt"]) if count_rows else 0
	except Exception as e:
		return flask.jsonify({"ok": False, "message": f"Failed to fetch count: {e}"}), 400
	return flask.jsonify({"count": count})
