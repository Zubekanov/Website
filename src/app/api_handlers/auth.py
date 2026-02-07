from __future__ import annotations

import flask
import bcrypt
from datetime import datetime, timezone

from app.api_context import ApiContext
from app.api_common import get_request_user, notify_moderators, send_notification_email
from util.user_management import UserManagement


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/profile/change-password", methods=["POST"])
	def api_profile_change_password():
		user = get_request_user(ctx)
		if not user:
			return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401

		data = flask.request.json or {}
		password = (data.get("password") or "").strip()
		confirm = (data.get("confirm_password") or "").strip()
		if not password or not confirm:
			return flask.jsonify({"ok": False, "message": "Please fill out both password fields."}), 400
		if password != confirm:
			return flask.jsonify({"ok": False, "message": "Passwords do not match."}), 400

		ok, msg = ctx.interface.update_user_password(user.get("id"), password)
		if not ok:
			return flask.jsonify({"ok": False, "message": msg}), 400
		return flask.jsonify({"ok": True, "message": "Password updated."})

	@api.route("/login", methods=["POST"])
	def api_login():
		data = flask.request.json or {}
		validation, message = UserManagement.login_user(
			email=data.get("email", ""),
			password=data.get("password", ""),
			remember_me=data.get("remember_me", False),
			ip=flask.request.remote_addr or "",
			user_agent=flask.request.headers.get("User-Agent", ""),
		)

		if validation:
			token = message
			message = "Login successful."
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
			key=ctx.auth_token_name,
			value=token,
			httponly=True,
			secure=True,
			samesite="Lax",
			max_age=30 * 24 * 60 * 60 if data.get("remember_me", False) else 24 * 60 * 60,
			path="/",
		)

		return resp, 200

	@api.route("/register", methods=["POST"])
	def api_register():
		data = flask.request.json or {}
		validation = UserManagement.validate_registration_fields(
			referral_source=data.get("referral_source", ""),
			first_name=data.get("first_name", ""),
			last_name=data.get("last_name", ""),
			email=data.get("email", ""),
			password=data.get("password", ""),
			repeat_password=data.get("repeat_password", ""),
		)
		if validation[0]:
			email = (data.get("email", "") or "").strip().lower()
			first_name = (data.get("first_name", "") or "").strip()
			last_name = (data.get("last_name", "") or "").strip()
			referral_source = (data.get("referral_source", "") or "").strip()
			notify_moderators(
				ctx,
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

	@api.route("/delete-account", methods=["POST"])
	def api_delete_account():
		user = get_request_user(ctx)
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

			ctx.interface.client.update_rows_with_filters(
				"users",
				{"is_active": False},
				raw_conditions=["id = %s"],
				raw_params=[user.get("id")],
			)
			ctx.interface.client.update_rows_with_filters(
				"user_sessions",
				{"revoked_at": datetime.now(timezone.utc)},
				raw_conditions=["user_id = %s", "revoked_at IS NULL"],
				raw_params=[user.get("id")],
			)
			ctx.interface.client.delete_rows_with_filters(
				"discord_webhooks",
				raw_conditions=["user_id = %s"],
				raw_params=[user.get("id")],
			)
			ctx.interface.client.delete_rows_with_filters(
				"minecraft_whitelist",
				raw_conditions=["user_id = %s"],
				raw_params=[user.get("id")],
			)
			ctx.interface.client.delete_rows_with_filters(
				"audiobookshelf_registrations",
				raw_conditions=["user_id = %s"],
				raw_params=[user.get("id")],
			)
			send_notification_email(
				to_email=user.get("email"),
				subject="Account deleted",
				title="Account deleted",
				intro="Your account has been deleted and access has been revoked.",
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		resp = flask.make_response(flask.jsonify({"ok": True, "message": "Account deleted."}))
		resp.set_cookie(
			key=ctx.auth_token_name,
			value="",
			httponly=True,
			secure=True,
			samesite="Lax",
			max_age=0,
			path="/",
		)
		return resp
