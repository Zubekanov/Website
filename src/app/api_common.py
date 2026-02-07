from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import flask

from app.api_context import ApiContext
from util.base_url import get_public_base_url as resolve_public_base_url
from util.integrations.discord.webhook_interface import DiscordWebhookEmitter
from util.integrations.email.email_interface import render_template, send_email

logger = logging.getLogger(__name__)


def get_request_user(ctx: ApiContext) -> dict | None:
	token = flask.request.cookies.get(ctx.auth_token_name)
	if not token:
		return None
	from util.user_management import UserManagement
	return UserManagement.get_user_by_session_token(token)


def require_admin(ctx: ApiContext) -> tuple[dict | None, tuple[Any, int] | None]:
	token = flask.request.cookies.get(ctx.auth_token_name)
	if not token:
		return None, (flask.jsonify({"ok": False, "message": "Authentication required."}), 401)
	from util.user_management import UserManagement
	user = UserManagement.get_user_by_session_token(token)
	if not user:
		return None, (flask.jsonify({"ok": False, "message": "Invalid session."}), 401)
	if not ctx.interface.is_admin(user.get("id")):
		return None, (flask.jsonify({"ok": False, "message": "Admin access required."}), 403)
	return user, None


def parse_db_value(value: object) -> object:
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


def get_table_meta(ctx: ApiContext, schema: str, table: str) -> tuple[list[str], list[str]]:
	if table not in ctx.interface.client.list_tables(schema):
		raise ValueError("Unknown table.")
	columns = ctx.interface.client.get_table_columns(schema, table)
	pk_cols = ctx.interface.client.get_primary_key_columns(schema, table)
	return columns, pk_cols


def discord_public_key(ctx: ApiContext) -> str | None:
	try:
		conf = ctx.fcr.find("secrets.conf")
		key = conf.get("DISCORD_WEBHOOK_INTERACTIONS_PUBLIC_KEY") if conf else None
		return key.strip() if key else None
	except Exception:
		return None


def verify_discord_signature(signature: str, timestamp: str, body: bytes, *, ctx: ApiContext) -> bool:
	try:
		from nacl.signing import VerifyKey
		from nacl.exceptions import BadSignatureError
	except Exception:
		return False
	key = discord_public_key(ctx)
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


def discord_interaction_response(content: str, *, ok: bool = True) -> dict:
	return {
		"type": 4,
		"data": {
			"content": content,
			"flags": 64,
		},
	}


def handle_mod_action(ctx: ApiContext, kind: str, action: str, reg_id: str) -> tuple[bool, str]:
	if kind == "audiobookshelf":
		return _handle_audiobookshelf_mod_action(ctx, action, reg_id)
	if kind == "discord-webhook":
		return _handle_discord_webhook_mod_action(ctx, action, reg_id)
	if kind == "minecraft":
		return _handle_minecraft_mod_action(ctx, action, reg_id)
	if kind == "api-access":
		return _handle_api_access_mod_action(ctx, action, reg_id)
	return False, "Unsupported action."


def notify_moderators(
	ctx: ApiContext,
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

		emitter = DiscordWebhookEmitter(ctx.interface)
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


def build_admin_action_buttons(kind: str, reg_id: str) -> list[dict]:
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


def get_user_email(ctx: ApiContext, user_id: str | None) -> str | None:
	if not user_id:
		return None
	try:
		rows, _ = ctx.interface.client.get_rows_with_filters(
			"users",
			equalities={"id": user_id},
			page_limit=1,
			page_num=0,
		)
		if rows:
			return (rows[0].get("email") or "").strip() or None
	except Exception:
		pass
	return None


def is_anonymous_user(ctx: ApiContext, user_id: str | None) -> bool:
	if not user_id:
		return False
	try:
		rows, _ = ctx.interface.client.get_rows_with_filters(
			"users",
			equalities={"id": user_id},
			page_limit=1,
			page_num=0,
		)
		if rows:
			return bool(rows[0].get("is_anonymous"))
	except Exception:
		pass
	return False


def get_public_base_url(ctx: ApiContext) -> str:
	return resolve_public_base_url(fcr=ctx.fcr)


def build_integration_removal_token(
	ctx: ApiContext,
	*,
	integration_type: str,
	integration_id: str,
	user_id: str,
	ttl_hours: int = 72,
) -> str:
	expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
	payload = {
		"type": integration_type,
		"id": integration_id,
		"user": user_id,
		"exp": int(expires_at.timestamp()),
	}
	raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
	sig = hmac.new(ctx.interface._token_secret(), raw.encode("utf-8"), hashlib.sha256).hexdigest()
	b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8").rstrip("=")
	return f"{b64}.{sig}"


def parse_integration_removal_token(ctx: ApiContext, token: str) -> dict | None:
	if not token or "." not in token:
		return None
	b64, sig = token.split(".", 1)
	try:
		padded = b64 + "=" * (-len(b64) % 4)
		raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
		expected = hmac.new(ctx.interface._token_secret(), raw.encode("utf-8"), hashlib.sha256).hexdigest()
		if not hmac.compare_digest(expected, sig):
			return None
		payload = json.loads(raw)
		if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
			return None
		return payload
	except Exception:
		return None


def send_notification_email(
	*,
	to_email: str | None,
	subject: str,
	title: str,
	intro: str,
	details: list[str] | None = None,
	cta_label: str | None = None,
	cta_url: str | None = None,
) -> None:
	if not to_email or "@" not in to_email:
		return
	detail_items = ""
	if details:
		for line in details:
			if line:
				detail_items += f"<li>{html.escape(line)}</li>"
	details_html = f"<ul class=\"detail-list\">{detail_items}</ul>" if detail_items else ""
	cta_html = ""
	if cta_label and cta_url:
		cta_html = (
			"<p>"
			f"<a class=\"button\" href=\"{html.escape(cta_url)}\">{html.escape(cta_label)}</a>"
			"</p>"
		)
	page_css = (
		".detail-list{margin:0.75rem 0 0;padding-left:1.2rem;color:#2b2f36;}"
		".detail-list li{margin:0.25rem 0;}"
	)
	html_payload = render_template("notification.html", {
		"title": html.escape(title),
		"intro": html.escape(intro),
		"details_html": details_html,
		"cta_html": cta_html,
		"page_css": page_css,
	})
	text_details = "\n".join(f"- {line}" for line in (details or []) if line)
	text_payload = intro
	if text_details:
		text_payload += "\n\nDetails:\n" + text_details
	if cta_label and cta_url:
		text_payload += f"\n\n{cta_label}: {cta_url}"
	try:
		send_email(
			to_addrs=[to_email],
			subject=subject,
			body_text=text_payload,
			body_html=html_payload,
		)
	except Exception:
		logger.exception("Failed to send notification email to %s", to_email)


def get_or_create_anonymous_user(
	ctx: ApiContext,
	*,
	first_name: str,
	last_name: str,
	email: str,
) -> tuple[bool, str | None]:
	email_norm = (email or "").strip().lower()
	first_norm = (first_name or "").strip()
	last_norm = (last_name or "").strip()
	if not email_norm or not first_norm or not last_norm:
		return False, "First name, last name, and email are required."

	# Ensure schema supports anonymous users.
	try:
		cols = ctx.interface.client.get_column_info("public", "users")
		if "is_anonymous" not in cols:
			ctx.interface.client.add_column("public", "users", "is_anonymous", "boolean DEFAULT false NOT NULL")
		if cols.get("password_hash") and str(cols["password_hash"].get("is_nullable", "")).upper() != "YES":
			ctx.interface.client.alter_column_nullability("public", "users", "password_hash", nullable=True)
	except Exception as e:
		return False, f"Failed to prepare users schema: {e}"

	try:
		rows = ctx.interface.get_user_by_email_case_insensitive(email_norm)
		if rows:
			row = rows[0]
			if "is_anonymous" not in row or not row.get("is_anonymous"):
				return False, "It looks like you already have an account. Please log in."
			if row.get("first_name") and row.get("last_name"):
				if str(row["first_name"]).strip().lower() == first_norm.lower() and str(row["last_name"]).strip().lower() == last_norm.lower():
					return True, str(row["id"])
			return False, "An anonymous account with this email exists under a different name. Please log in."

		row = ctx.interface.client.insert_row("users", {
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


def _handle_audiobookshelf_mod_action(ctx: ApiContext, action: str, reg_id: str) -> tuple[bool, str]:
	if action == "approve":
		try:
			ctx.interface.client.update_rows_with_filters(
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
			ctx.interface.client.update_rows_with_filters(
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
	return False, "Unsupported action."


def _handle_discord_webhook_mod_action(ctx: ApiContext, action: str, reg_id: str) -> tuple[bool, str]:
	if action == "approve":
		emitter = DiscordWebhookEmitter(ctx.interface)
		return emitter.approve_registration(registration_id=reg_id, reviewer_user_id=None)
	if action == "deny":
		try:
			ctx.interface.client.update_rows_with_filters(
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
	return False, "Unsupported action."


def _handle_minecraft_mod_action(ctx: ApiContext, action: str, reg_id: str) -> tuple[bool, str]:
	if action == "approve":
		try:
			rows = ctx.interface.get_minecraft_registration_by_id(reg_id)
			if rows:
				reg = rows[0]
				existing = ctx.interface.get_minecraft_whitelist_by_username(reg.get("mc_username"))
				if not existing:
					ctx.interface.client.insert_row("minecraft_whitelist", {
						"user_id": reg.get("user_id"),
						"first_name": reg.get("first_name"),
						"last_name": reg.get("last_name"),
						"email": reg.get("email"),
						"mc_username": reg.get("mc_username"),
						"is_active": True,
					})
				else:
					ctx.interface.client.update_rows_with_filters(
						"minecraft_whitelist",
						{"is_active": True, "ban_reason": None},
						raw_conditions=["id = %s"],
						raw_params=[existing[0]["id"]],
					)
			ctx.interface.client.update_rows_with_filters(
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
			ctx.interface.client.update_rows_with_filters(
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


def _handle_api_access_mod_action(ctx: ApiContext, action: str, reg_id: str) -> tuple[bool, str]:
	if action == "approve":
		try:
			ctx.interface.client.update_rows_with_filters(
				"api_access_registrations",
				{
					"status": "approved",
					"is_active": True,
					"reviewed_at": datetime.now(timezone.utc),
				},
				raw_conditions=["id = %s"],
				raw_params=[reg_id],
			)
			return True, "API access request approved."
		except Exception as e:
			return False, f"Approve failed: {e}"
	if action == "deny":
		try:
			ctx.interface.client.update_rows_with_filters(
				"api_access_registrations",
				{
					"status": "denied",
					"is_active": False,
					"reviewed_at": datetime.now(timezone.utc),
				},
				raw_conditions=["id = %s"],
				raw_params=[reg_id],
			)
			return True, "API access request denied."
		except Exception as e:
			return False, f"Deny failed: {e}"
	return False, "Unsupported action."
