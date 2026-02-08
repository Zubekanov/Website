from __future__ import annotations

import html as _html
import logging
from datetime import datetime, timezone

import flask

from app.api_context import ApiContext
from app.api_common import (
	build_integration_removal_token,
	get_public_base_url,
	get_table_meta,
	get_user_email,
	is_anonymous_user,
	notify_moderators,
	parse_db_value,
	require_admin,
	send_notification_email,
)
from util.integrations.discord.webhook_interface import DiscordWebhookEmitter
from util.integrations.email.email_interface import render_template, send_email
from util.integrations.minecraft.amp_interface import (
	AmpMinecraftClient,
	load_amp_minecraft_config,
)
from util.verification_utils import build_verification_expiry_text

logger = logging.getLogger(__name__)


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/admin/users/promote", methods=["POST"])
	def api_admin_users_promote():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		user_id = (data.get("user_id") or "").strip()
		if not user_id:
			return flask.jsonify({"ok": False, "message": "Missing user id."}), 400
		rows, _ = ctx.interface.client.get_rows_with_filters(
			"users",
			equalities={"id": user_id},
			page_limit=1,
			page_num=0,
		)
		if not rows:
			return flask.jsonify({"ok": False, "message": "User not found."}), 404
		ok, message = ctx.interface.promote_user_to_admin(user_id)
		if not ok:
			return flask.jsonify({"ok": False, "message": message}), 400
		target_email = get_user_email(ctx, user_id)
		send_notification_email(
			to_email=target_email,
			subject="Role updated: Admin access granted",
			title="Admin access granted",
			intro="Your account has been granted admin access.",
		)
		notify_moderators(
			ctx,
			"role_granted",
			title="Admin role granted",
			actor=user.get("email") or user.get("id"),
			subject=user_id,
			details=[
				f"User ID: {user_id}",
			],
			context={
				"action": "role_granted",
				"role": "admin",
				"user_id": user_id,
				"reviewer_user_id": user.get("id"),
			},
		)
		return flask.jsonify({"ok": True, "message": message})

	@api.route("/api/admin/users/demote", methods=["POST"])
	def api_admin_users_demote():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		user_id = (data.get("user_id") or "").strip()
		if not user_id:
			return flask.jsonify({"ok": False, "message": "Missing user id."}), 400
		rows, _ = ctx.interface.client.get_rows_with_filters(
			"users",
			equalities={"id": user_id},
			page_limit=1,
			page_num=0,
		)
		if not rows:
			return flask.jsonify({"ok": False, "message": "User not found."}), 404
		ok, message = ctx.interface.demote_user_from_admin(user_id)
		if not ok:
			return flask.jsonify({"ok": False, "message": message}), 400
		target_email = get_user_email(ctx, user_id)
		send_notification_email(
			to_email=target_email,
			subject="Role updated: Admin access revoked",
			title="Admin access revoked",
			intro="Your account has been reverted to a standard member role.",
		)
		notify_moderators(
			ctx,
			"role_revoked",
			title="Admin role revoked",
			actor=user.get("email") or user.get("id"),
			subject=user_id,
			details=[
				f"User ID: {user_id}",
			],
			context={
				"action": "role_revoked",
				"role": "admin",
				"user_id": user_id,
				"reviewer_user_id": user.get("id"),
			},
		)
		return flask.jsonify({"ok": True, "message": message})

	@api.route("/api/admin/users/integration/disable", methods=["POST"])
	def api_admin_users_integration_disable():
		admin_user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		integration_type = (data.get("integration_type") or "").strip().lower()
		integration_id = (data.get("integration_id") or "").strip()
		target_user_id = (data.get("user_id") or "").strip()
		reason = (data.get("reason") or "").strip()
		confirmed = bool(data.get("confirm"))
		if not integration_type or not integration_id or not target_user_id:
			return flask.jsonify({"ok": False, "message": "Missing integration details."}), 400
		if not confirmed:
			return flask.jsonify({"ok": False, "message": "Please confirm deletion."}), 400
		if not reason:
			return flask.jsonify({"ok": False, "message": "Please select a reason."}), 400

		try:
			if integration_type == "discord_webhook":
				rows = ctx.interface.get_discord_webhook_for_user(integration_id, target_user_id)
				if not rows:
					return flask.jsonify({"ok": False, "message": "Webhook not found."}), 404
				ctx.interface.client.update_rows_with_filters(
					"discord_webhooks",
					{"is_active": False},
					raw_conditions=["id = %s"],
					raw_params=[integration_id],
				)
				target_email = get_user_email(ctx, target_user_id)
				send_notification_email(
					to_email=target_email,
					subject="Discord webhook disabled by admin",
					title="Discord webhook disabled",
					intro="An administrator has disabled your Discord webhook integration.",
					details=[
						f"Reason: {reason}",
					],
				)
				notify_moderators(
					ctx,
					"integration_disabled",
					title="Integration disabled by admin",
					actor=admin_user.get("email") or admin_user.get("id"),
					subject="Discord Webhook",
					details=[
						f"Integration ID: {integration_id}",
						f"Target user: {target_user_id}",
						f"Reason: {reason}",
					],
					context={
						"action": "integration_disabled",
						"integration_type": "discord_webhook",
						"integration_id": integration_id,
						"user_id": target_user_id,
						"reason": reason,
						"admin_user_id": admin_user.get("id"),
					},
				)
				return flask.jsonify({"ok": True, "message": "Webhook disabled."})
			if integration_type == "minecraft":
				rows = ctx.interface.get_minecraft_whitelist_entry_for_user(integration_id, target_user_id)
				if not rows:
					return flask.jsonify({"ok": False, "message": "Minecraft whitelist entry not found."}), 404
				mc_rows = ctx.interface.get_minecraft_whitelist_username_by_id(integration_id)
				mc_username = mc_rows[0].get("mc_username") if mc_rows else None
				existing_reason = (rows[0].get("ban_reason") or "").strip()
				note = f"Disabled by admin: {reason}"
				combined_reason = existing_reason
				if note not in existing_reason:
					combined_reason = (existing_reason + "\n" + note).strip() if existing_reason else note
				ctx.interface.client.update_rows_with_filters(
					"minecraft_whitelist",
					{"is_active": False, "ban_reason": combined_reason},
					raw_conditions=["id = %s"],
					raw_params=[integration_id],
				)
				target_email = get_user_email(ctx, target_user_id)
				send_notification_email(
					to_email=target_email,
					subject="Minecraft integration disabled by admin",
					title="Minecraft integration disabled",
					intro="An administrator has disabled your Minecraft whitelist integration.",
					details=[
						f"Username: {mc_username}" if mc_username else "",
						f"Reason: {reason}",
					],
				)
				notify_moderators(
					ctx,
					"integration_disabled",
					title="Integration disabled by admin",
					actor=admin_user.get("email") or admin_user.get("id"),
					subject="Minecraft",
					details=[
						f"Integration ID: {integration_id}",
						f"Target user: {target_user_id}",
						f"Reason: {reason}",
					],
					context={
						"action": "integration_disabled",
						"integration_type": "minecraft",
						"integration_id": integration_id,
						"user_id": target_user_id,
						"reason": reason,
						"admin_user_id": admin_user.get("id"),
					},
				)
				return flask.jsonify({"ok": True, "message": "Minecraft integration disabled."})
			if integration_type == "audiobookshelf":
				rows = ctx.interface.get_audiobookshelf_registration_for_user(integration_id, target_user_id)
				if not rows:
					return flask.jsonify({"ok": False, "message": "Audiobookshelf integration not found."}), 404
				ctx.interface.client.update_rows_with_filters(
					"audiobookshelf_registrations",
					{"is_active": False},
					raw_conditions=["id = %s"],
					raw_params=[integration_id],
				)
				target_email = get_user_email(ctx, target_user_id)
				send_notification_email(
					to_email=target_email,
					subject="Audiobookshelf integration disabled by admin",
					title="Audiobookshelf integration disabled",
					intro="An administrator has disabled your Audiobookshelf integration.",
					details=[
						f"Reason: {reason}",
					],
				)
				notify_moderators(
					ctx,
					"integration_disabled",
					title="Integration disabled by admin",
					actor=admin_user.get("email") or admin_user.get("id"),
					subject="Audiobookshelf",
					details=[
						f"Integration ID: {integration_id}",
						f"Target user: {target_user_id}",
						f"Reason: {reason}",
					],
					context={
						"action": "integration_disabled",
						"integration_type": "audiobookshelf",
						"integration_id": integration_id,
						"user_id": target_user_id,
						"reason": reason,
						"admin_user_id": admin_user.get("id"),
					},
				)
				return flask.jsonify({"ok": True, "message": "Audiobookshelf integration disabled."})
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		return flask.jsonify({"ok": False, "message": "Unknown integration type."}), 400

	@api.route("/api/admin/users/integration/enable", methods=["POST"])
	def api_admin_users_integration_enable():
		admin_user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		integration_type = (data.get("integration_type") or "").strip().lower()
		integration_id = (data.get("integration_id") or "").strip()
		target_user_id = (data.get("user_id") or "").strip()
		if not integration_type or not integration_id or not target_user_id:
			return flask.jsonify({"ok": False, "message": "Missing integration details."}), 400

		try:
			if integration_type == "discord_webhook":
				rows = ctx.interface.get_discord_webhook_for_user(integration_id, target_user_id)
				if not rows:
					return flask.jsonify({"ok": False, "message": "Webhook not found."}), 404
				ctx.interface.client.update_rows_with_filters(
					"discord_webhooks",
					{"is_active": True},
					raw_conditions=["id = %s"],
					raw_params=[integration_id],
				)
				ctx.interface.client.delete_rows_with_filters(
					"application_exemptions",
					raw_conditions=["user_id = %s", "integration_type = 'discord_webhook'"],
					raw_params=[target_user_id],
				)
				notify_moderators(
					ctx,
					"integration_enabled",
					title="Integration enabled by admin",
					actor=admin_user.get("email") or admin_user.get("id"),
					subject="Discord Webhook",
					details=[
						f"Integration ID: {integration_id}",
						f"Target user: {target_user_id}",
					],
					context={
						"action": "integration_enabled",
						"integration_type": "discord_webhook",
						"integration_id": integration_id,
						"user_id": target_user_id,
						"admin_user_id": admin_user.get("id"),
					},
				)
				return flask.jsonify({"ok": True, "message": "Webhook enabled."})
			if integration_type == "minecraft":
				rows = ctx.interface.get_minecraft_whitelist_entry_for_user(integration_id, target_user_id)
				if not rows:
					return flask.jsonify({"ok": False, "message": "Minecraft whitelist entry not found."}), 404
				mc_username = None
				mc_rows = ctx.interface.get_minecraft_whitelist_username_by_id(integration_id)
				if mc_rows:
					mc_username = mc_rows[0].get("mc_username")
				ctx.interface.client.update_rows_with_filters(
					"minecraft_whitelist",
					{"is_active": True, "ban_reason": None},
					raw_conditions=["id = %s"],
					raw_params=[integration_id],
				)
				if mc_username:
					ctx.interface.client.delete_rows_with_filters(
						"application_exemptions",
						raw_conditions=["user_id = %s", "integration_type = 'minecraft'", "integration_key = %s"],
						raw_params=[target_user_id, mc_username],
					)
				notify_moderators(
					ctx,
					"integration_enabled",
					title="Integration enabled by admin",
					actor=admin_user.get("email") or admin_user.get("id"),
					subject="Minecraft",
					details=[
						f"Integration ID: {integration_id}",
						f"Target user: {target_user_id}",
					],
					context={
						"action": "integration_enabled",
						"integration_type": "minecraft",
						"integration_id": integration_id,
						"user_id": target_user_id,
						"admin_user_id": admin_user.get("id"),
					},
				)
				return flask.jsonify({"ok": True, "message": "Minecraft integration enabled."})
			if integration_type == "audiobookshelf":
				rows = ctx.interface.get_audiobookshelf_registration_for_user(integration_id, target_user_id)
				if not rows:
					return flask.jsonify({"ok": False, "message": "Audiobookshelf integration not found."}), 404
				ctx.interface.client.update_rows_with_filters(
					"audiobookshelf_registrations",
					{"is_active": True, "status": "approved"},
					raw_conditions=["id = %s"],
					raw_params=[integration_id],
				)
				ctx.interface.client.delete_rows_with_filters(
					"application_exemptions",
					raw_conditions=["user_id = %s", "integration_type = 'audiobookshelf'"],
					raw_params=[target_user_id],
				)
				notify_moderators(
					ctx,
					"integration_enabled",
					title="Integration enabled by admin",
					actor=admin_user.get("email") or admin_user.get("id"),
					subject="Audiobookshelf",
					details=[
						f"Integration ID: {integration_id}",
						f"Target user: {target_user_id}",
					],
					context={
						"action": "integration_enabled",
						"integration_type": "audiobookshelf",
						"integration_id": integration_id,
						"user_id": target_user_id,
						"admin_user_id": admin_user.get("id"),
					},
				)
				return flask.jsonify({"ok": True, "message": "Audiobookshelf integration enabled."})
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		return flask.jsonify({"ok": False, "message": "Unknown integration type."}), 400

	@api.route("/api/admin/users/delete", methods=["POST"])
	def api_admin_users_delete():
		admin_user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		target_user_id = (data.get("user_id") or "").strip()
		reason = (data.get("reason") or "").strip()
		confirmed = bool(data.get("confirm"))
		if not target_user_id:
			return flask.jsonify({"ok": False, "message": "Missing user id."}), 400
		if not confirmed:
			return flask.jsonify({"ok": False, "message": "Please confirm deletion."}), 400
		if not reason:
			return flask.jsonify({"ok": False, "message": "Please select a reason."}), 400

		try:
			user_rows, _ = ctx.interface.client.get_rows_with_filters(
				"users",
				equalities={"id": target_user_id},
				page_limit=1,
				page_num=0,
			)
			if not user_rows:
				return flask.jsonify({"ok": False, "message": "User not found."}), 404
			target_user = user_rows[0]

			ctx.interface.client.update_rows_with_filters(
				"users",
				{"is_active": False},
				raw_conditions=["id = %s"],
				raw_params=[target_user_id],
			)
			ctx.interface.client.update_rows_with_filters(
				"user_sessions",
				{"revoked_at": datetime.now(timezone.utc)},
				raw_conditions=["user_id = %s", "revoked_at IS NULL"],
				raw_params=[target_user_id],
			)
			ctx.interface.client.delete_rows_with_filters(
				"discord_webhooks",
				raw_conditions=["user_id = %s"],
				raw_params=[target_user_id],
			)
			ctx.interface.client.delete_rows_with_filters(
				"minecraft_whitelist",
				raw_conditions=["user_id = %s"],
				raw_params=[target_user_id],
			)
			ctx.interface.client.delete_rows_with_filters(
				"audiobookshelf_registrations",
				raw_conditions=["user_id = %s"],
				raw_params=[target_user_id],
			)
			send_notification_email(
				to_email=target_user.get("email"),
				subject="Account deleted by admin",
				title="Account deleted",
				intro="An administrator has deleted your account and revoked access.",
				details=[
					f"Reason: {reason}",
				],
			)
			notify_moderators(
				ctx,
				"account_deleted",
				title="Account disabled by admin",
				actor=admin_user.get("email") or admin_user.get("id"),
				subject=target_user.get("email") or target_user_id,
				details=[
					f"User ID: {target_user_id}",
					f"Reason: {reason}",
				],
				context={
					"action": "account_deleted",
					"user_id": target_user_id,
					"reason": reason,
					"admin_user_id": admin_user.get("id"),
				},
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		return flask.jsonify({"ok": True, "message": "Account deleted."})

	@api.route("/api/admin/email/debug", methods=["POST"])
	def api_admin_send_debug_email():
		user, err = require_admin(ctx)
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
				base_url = get_public_base_url(ctx)
				verify_url = f"{base_url.rstrip('/')}/verify-email/{dummy_code}"
				expiry_text = "This link may be invalid due to a server error."
				try:
					token_hash = ctx.interface._hash_verification_token(dummy_code)
					rows, _ = ctx.interface.client.get_rows_with_filters(
						"pending_users",
						equalities={"verification_token_hash": token_hash},
						page_limit=1,
						page_num=0,
					)
					expires_at = rows[0].get("token_expires_at") if rows else None
					expiry_text = build_verification_expiry_text(expires_at)
				except Exception:
					pass

				html_payload = render_template("verify_email.html", {
					"verify_url": verify_url,
					"expiry_text": expiry_text,
				})
				text_payload = (
					"Someone has created an account with this email address. If this was you, "
					"click the button below to verify your email address.\n\n"
					f"Verification button: {verify_url}\n\n"
					f"{expiry_text}\n\n"
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
				return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 500

			if not result.ok:
				return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 502
			return flask.jsonify({"ok": True, "message": "Verification email sent."}), 200

		if not subject:
			return flask.jsonify({"ok": False, "message": "Subject is required."}), 400
		if not body:
			return flask.jsonify({"ok": False, "message": "Body is required."}), 400

		try:
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
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 500

		if not result.ok:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 502
		return flask.jsonify({"ok": True, "message": "Email sent."}), 200

	@api.route("/api/admin/db/update-row", methods=["POST"])
	def api_admin_db_update_row():
		user, err = require_admin(ctx)
		if err:
			return err

		data = flask.request.json or {}
		table = str(data.get("table", "")).strip()
		schema = str(data.get("schema", "public")).strip() or "public"
		if not table:
			return flask.jsonify({"ok": False, "message": "Missing table."}), 400

		try:
			columns, pk_cols = get_table_meta(ctx, schema, table)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Invalid request."}), 400

		if not pk_cols:
			return flask.jsonify({"ok": False, "message": "Table has no primary key."}), 400

		updates = {}
		for key, value in data.items():
			if not key.startswith("col__"):
				continue
			col = key[5:]
			if col in columns:
				updates[col] = parse_db_value(value)

		if not updates:
			return flask.jsonify({"ok": False, "message": "No fields to update."}), 400

		equalities = {}
		for col in pk_cols:
			pk_val = data.get(f"pk__{col}")
			if pk_val is None:
				return flask.jsonify({"ok": False, "message": f"Missing primary key value: {col}."}), 400
			equalities[col] = parse_db_value(pk_val)

		try:
			updated = ctx.interface.client.update_rows_with_equalities(f"{schema}.{table}", updates, equalities)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

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
		user, err = require_admin(ctx)
		if err:
			return err

		data = flask.request.json or {}
		table = str(data.get("table", "")).strip()
		schema = str(data.get("schema", "public")).strip() or "public"
		if not table:
			return flask.jsonify({"ok": False, "message": "Missing table."}), 400

		try:
			columns, pk_cols = get_table_meta(ctx, schema, table)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Invalid request."}), 400

		if not pk_cols:
			return flask.jsonify({"ok": False, "message": "Table has no primary key."}), 400

		equalities = {}
		for col in pk_cols:
			pk_val = data.get(f"pk__{col}")
			if pk_val is None:
				return flask.jsonify({"ok": False, "message": f"Missing primary key value: {col}."}), 400
			equalities[col] = parse_db_value(pk_val)

		try:
			deleted = ctx.interface.client.delete_rows_with_filters(f"{schema}.{table}", equalities=equalities)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

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
			notify_moderators(
				ctx,
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
		user, err = require_admin(ctx)
		if err:
			return err

		data = flask.request.json or {}
		table = str(data.get("table", "")).strip()
		schema = str(data.get("schema", "public")).strip() or "public"
		if not table:
			return flask.jsonify({"ok": False, "message": "Missing table."}), 400

		try:
			columns, _ = get_table_meta(ctx, schema, table)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Invalid request."}), 400

		inserts = {}
		for key, value in data.items():
			if not key.startswith("col__"):
				continue
			col = key[5:]
			if col not in columns:
				continue
			val = parse_db_value(value)
			if val is None:
				continue
			inserts[col] = val

		if not inserts:
			return flask.jsonify({"ok": False, "message": "No fields to insert."}), 400

		try:
			row = ctx.interface.client.insert_row(f"{schema}.{table}", inserts)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		ctx.interface.logger.info(
			"Admin DB insert by user_id=%s table=%s.%s inserts=%s",
			user.get("id"),
			schema,
			table,
			inserts,
		)
		if schema == "public" and table == "admins":
			notify_moderators(
				ctx,
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

	@api.route("/api/admin/audiobookshelf/approve", methods=["POST"])
	def api_admin_audiobookshelf_approve():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		reg_id = (data.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_audiobookshelf_registration_contact_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			updated = ctx.interface.client.update_rows_with_filters(
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
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		if updated == 0:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		anon_user = is_anonymous_user(ctx, reg.get("user_id"))
		cta_label = None
		cta_url = None
		intro = "Your Audiobookshelf registration has been approved."
		if anon_user:
			token = build_integration_removal_token(
				ctx,
				integration_type="audiobookshelf",
				integration_id=str(reg_id),
				user_id=str(reg.get("user_id")),
			)
			intro += " This integration was created without a linked account on zubekanov.com."
			cta_label = "Remove integration"
			cta_url = f"{get_public_base_url(ctx).rstrip('/')}/integration/remove?token={token}"
		send_notification_email(
			to_email=reg.get("email"),
			subject="Audiobookshelf access approved",
			title="Audiobookshelf approved",
			intro=intro,
			cta_label=cta_label,
			cta_url=cta_url,
		)
		notify_moderators(
			ctx,
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
		user, err = require_admin(ctx)
		if err:
			return err
		reg_id = (flask.request.args.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			reg_rows = ctx.interface.get_audiobookshelf_registration_contact_by_id(reg_id)
			if not reg_rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = reg_rows[0]
			ctx.interface.client.update_rows_with_filters(
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
			anon_user = is_anonymous_user(ctx, reg.get("user_id"))
			cta_label = None
			cta_url = None
			intro = "Your Audiobookshelf registration has been approved."
			if anon_user:
				token = build_integration_removal_token(
					ctx,
					integration_type="audiobookshelf",
					integration_id=str(reg_id),
					user_id=str(reg.get("user_id")),
				)
				intro += " This integration was created without a linked account on zubekanov.com."
				cta_label = "Remove integration"
				cta_url = f"{get_public_base_url(ctx).rstrip('/')}/integration/remove?token={token}"
			send_notification_email(
				to_email=reg.get("email"),
				subject="Audiobookshelf access approved",
				title="Audiobookshelf approved",
				intro=intro,
				cta_label=cta_label,
				cta_url=cta_url,
			)
			notify_moderators(
				ctx,
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
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.redirect("/admin/audiobookshelf-approvals")

	@api.route("/api/admin/audiobookshelf/deny", methods=["POST"])
	def api_admin_audiobookshelf_deny():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		reg_id = (data.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_audiobookshelf_registration_contact_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			updated = ctx.interface.client.update_rows_with_filters(
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
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		if updated == 0:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		notify_moderators(
			ctx,
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
		user, err = require_admin(ctx)
		if err:
			return err
		reg_id = (flask.request.args.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_audiobookshelf_registration_contact_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			ctx.interface.client.update_rows_with_filters(
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
			notify_moderators(
				ctx,
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
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.redirect("/admin/audiobookshelf-approvals")

	@api.route("/api/admin/discord-webhook/approve", methods=["POST"])
	def api_admin_discord_webhook_approve():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		reg_id = (data.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_discord_webhook_registration_contact_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			emitter = DiscordWebhookEmitter(ctx.interface)
			ok, msg = emitter.approve_registration(
				registration_id=reg_id,
				reviewer_user_id=user.get("id"),
			)
			if not ok:
				return flask.jsonify({"ok": False, "message": msg}), 400
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		target_email = get_user_email(ctx, reg.get("submitted_by_user_id")) or reg.get("submitted_by_email")
		anon_user = is_anonymous_user(ctx, reg.get("submitted_by_user_id"))
		cta_label = None
		cta_url = None
		intro = "Your Discord webhook subscription has been approved and activated."
		if anon_user:
			intro += " This integration was created without a linked account on zubekanov.com."
			webhook_rows = ctx.interface.get_discord_webhook_id_by_url_and_user(
				reg.get("webhook_url"),
				reg.get("submitted_by_user_id"),
			)
			if webhook_rows:
				webhook_id = str(webhook_rows[0].get("id"))
				token = build_integration_removal_token(
					ctx,
					integration_type="discord_webhook",
					integration_id=webhook_id,
					user_id=str(reg.get("submitted_by_user_id")),
				)
				cta_label = "Remove integration"
				cta_url = f"{get_public_base_url(ctx).rstrip('/')}/integration/remove?token={token}"
		send_notification_email(
			to_email=target_email,
			subject="Webhook subscription approved",
			title="Webhook subscription approved",
			intro=intro,
			details=[
				f"Webhook: {reg.get('name') or reg.get('webhook_url')}",
				f"Event key: {reg.get('event_key')}",
			],
			cta_label=cta_label,
			cta_url=cta_url,
		)
		notify_moderators(
			ctx,
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
		user, err = require_admin(ctx)
		if err:
			return err
		reg_id = (flask.request.args.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_discord_webhook_registration_contact_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			emitter = DiscordWebhookEmitter(ctx.interface)
			ok, msg = emitter.approve_registration(
				registration_id=reg_id,
				reviewer_user_id=user.get("id"),
			)
			if not ok:
				return flask.jsonify({"ok": False, "message": msg}), 400
			target_email = get_user_email(ctx, reg.get("submitted_by_user_id")) or reg.get("submitted_by_email")
			anon_user = is_anonymous_user(ctx, reg.get("submitted_by_user_id"))
			cta_label = None
			cta_url = None
			intro = "Your Discord webhook subscription has been approved and activated."
			if anon_user:
				intro += " This integration was created without a linked account on zubekanov.com."
				webhook_rows = ctx.interface.get_discord_webhook_id_by_url_and_user(
					reg.get("webhook_url"),
					reg.get("submitted_by_user_id"),
				)
				if webhook_rows:
					webhook_id = str(webhook_rows[0].get("id"))
					token = build_integration_removal_token(
						ctx,
						integration_type="discord_webhook",
						integration_id=webhook_id,
						user_id=str(reg.get("submitted_by_user_id")),
					)
					cta_label = "Remove integration"
					cta_url = f"{get_public_base_url(ctx).rstrip('/')}/integration/remove?token={token}"
			send_notification_email(
				to_email=target_email,
				subject="Webhook subscription approved",
				title="Webhook subscription approved",
				intro=intro,
				details=[
					f"Webhook: {reg.get('name') or reg.get('webhook_url')}",
					f"Event key: {reg.get('event_key')}",
				],
				cta_label=cta_label,
				cta_url=cta_url,
			)
			notify_moderators(
				ctx,
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
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.redirect("/admin/discord-webhook-approvals")

	@api.route("/api/admin/discord-webhook/deny", methods=["POST"])
	def api_admin_discord_webhook_deny():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		reg_id = (data.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_discord_webhook_registration_basic_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			updated = ctx.interface.client.update_rows_with_filters(
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
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		if updated == 0:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		notify_moderators(
			ctx,
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
		user, err = require_admin(ctx)
		if err:
			return err
		reg_id = (flask.request.args.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_discord_webhook_registration_basic_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			ctx.interface.client.update_rows_with_filters(
				"discord_webhook_registrations",
				{
					"status": "denied",
					"reviewed_at": datetime.now(timezone.utc),
					"reviewed_by_user_id": user.get("id"),
				},
				raw_conditions=["id = %s"],
				raw_params=[reg_id],
			)
			notify_moderators(
				ctx,
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
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.redirect("/admin/discord-webhook-approvals")

	@api.route("/api/admin/audiobookshelf/pending-count")
	def api_admin_audiobookshelf_pending_count():
		_, err = require_admin(ctx)
		if err:
			return err
		try:
			count = ctx.interface.count_pending_audiobookshelf_registrations()
			if count is None:
				raise RuntimeError("Count unavailable")
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.jsonify({"count": count})

	@api.route("/api/admin/discord-webhook/pending-count")
	def api_admin_discord_webhook_pending_count():
		_, err = require_admin(ctx)
		if err:
			return err
		try:
			count = ctx.interface.count_pending_discord_webhook_registrations()
			if count is None:
				raise RuntimeError("Count unavailable")
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.jsonify({"count": count})

	@api.route("/api/admin/api-access/approve", methods=["POST"])
	def api_admin_api_access_approve():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		reg_id = (data.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_api_access_registration_contact_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			updated = ctx.interface.client.update_rows_with_filters(
				"api_access_registrations",
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
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		if updated == 0:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		scopes = reg.get("requested_scopes") or []
		if isinstance(scopes, list):
			scopes_text = ", ".join(str(s) for s in scopes if s)
		else:
			scopes_text = str(scopes)
		send_notification_email(
			to_email=reg.get("email"),
			subject="API access request approved",
			title="API access approved",
			intro="Your API access request has been approved.",
			details=[
				f"Principal type: {reg.get('principal_type') or '—'}",
				f"Service name: {reg.get('service_name') or '—'}",
				f"Scopes: {scopes_text or '—'}",
			],
		)
		notify_moderators(
			ctx,
			"api_access_request_approved",
			title="API access request approved",
			actor=user.get("email") or user.get("id"),
			subject=f"{reg.get('first_name', '')} {reg.get('last_name', '')}".strip() or reg.get("email"),
			details=[
				f"Email: {reg.get('email', '')}",
				f"Request ID: {reg_id}",
			],
			context={
				"action": "api_access_request_approved",
				"reviewer_user_id": user.get("id"),
				"request_id": reg_id,
			},
		)
		return flask.jsonify({"ok": True, "message": "Approved."})

	@api.route("/api/admin/api-access/approve-link")
	def api_admin_api_access_approve_link():
		user, err = require_admin(ctx)
		if err:
			return err
		reg_id = (flask.request.args.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_api_access_registration_contact_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			ctx.interface.client.update_rows_with_filters(
				"api_access_registrations",
				{
					"status": "approved",
					"is_active": True,
					"reviewed_at": datetime.now(timezone.utc),
					"reviewed_by_user_id": user.get("id"),
				},
				raw_conditions=["id = %s"],
				raw_params=[reg_id],
			)
			scopes = reg.get("requested_scopes") or []
			if isinstance(scopes, list):
				scopes_text = ", ".join(str(s) for s in scopes if s)
			else:
				scopes_text = str(scopes)
			send_notification_email(
				to_email=reg.get("email"),
				subject="API access request approved",
				title="API access approved",
				intro="Your API access request has been approved.",
				details=[
					f"Principal type: {reg.get('principal_type') or '—'}",
					f"Service name: {reg.get('service_name') or '—'}",
					f"Scopes: {scopes_text or '—'}",
				],
			)
			notify_moderators(
				ctx,
				"api_access_request_approved",
				title="API access request approved",
				actor=user.get("email") or user.get("id"),
				subject=f"{reg.get('first_name', '')} {reg.get('last_name', '')}".strip() or reg.get("email"),
				details=[
					f"Email: {reg.get('email', '')}",
					f"Request ID: {reg_id}",
				],
				context={
					"action": "api_access_request_approved",
					"reviewer_user_id": user.get("id"),
					"request_id": reg_id,
				},
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.redirect("/admin/api-access-approvals")

	@api.route("/api/admin/api-access/deny", methods=["POST"])
	def api_admin_api_access_deny():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		reg_id = (data.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_api_access_registration_contact_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			updated = ctx.interface.client.update_rows_with_filters(
				"api_access_registrations",
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
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		if updated == 0:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		send_notification_email(
			to_email=reg.get("email"),
			subject="API access request denied",
			title="API access denied",
			intro="Your API access request was denied.",
		)
		notify_moderators(
			ctx,
			"api_access_request_denied",
			title="API access request denied",
			actor=user.get("email") or user.get("id"),
			subject=f"{reg.get('first_name', '')} {reg.get('last_name', '')}".strip() or reg.get("email"),
			details=[
				f"Email: {reg.get('email', '')}",
				f"Request ID: {reg_id}",
			],
			context={
				"action": "api_access_request_denied",
				"reviewer_user_id": user.get("id"),
				"request_id": reg_id,
			},
		)
		return flask.jsonify({"ok": True, "message": "Denied."})

	@api.route("/api/admin/api-access/deny-link")
	def api_admin_api_access_deny_link():
		user, err = require_admin(ctx)
		if err:
			return err
		reg_id = (flask.request.args.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_api_access_registration_contact_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			ctx.interface.client.update_rows_with_filters(
				"api_access_registrations",
				{
					"status": "denied",
					"is_active": False,
					"reviewed_at": datetime.now(timezone.utc),
					"reviewed_by_user_id": user.get("id"),
				},
				raw_conditions=["id = %s"],
				raw_params=[reg_id],
			)
			notify_moderators(
				ctx,
				"api_access_request_denied",
				title="API access request denied",
				actor=user.get("email") or user.get("id"),
				subject=f"{reg.get('first_name', '')} {reg.get('last_name', '')}".strip() or reg.get("email"),
				details=[
					f"Email: {reg.get('email', '')}",
					f"Request ID: {reg_id}",
				],
				context={
					"action": "api_access_request_denied",
					"reviewer_user_id": user.get("id"),
					"request_id": reg_id,
				},
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.redirect("/admin/api-access-approvals")

	@api.route("/api/admin/api-access/pending-count")
	def api_admin_api_access_pending_count():
		_, err = require_admin(ctx)
		if err:
			return err
		try:
			count = ctx.interface.count_pending_api_access_registrations()
			if count is None:
				raise RuntimeError("Count unavailable")
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.jsonify({"count": count})

	@api.route("/api/admin/minecraft/approve", methods=["POST"])
	def api_admin_minecraft_approve():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		reg_id = (data.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_minecraft_registration_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
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
					"reviewed_by_user_id": user.get("id"),
				},
				raw_conditions=["id = %s"],
				raw_params=[reg_id],
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		anon_user = is_anonymous_user(ctx, reg.get("user_id"))
		cta_label = None
		cta_url = None
		intro = "Your Minecraft whitelist request has been approved."
		if anon_user:
			intro += " This integration was created without a linked account on zubekanov.com."
			whitelist_rows = ctx.interface.get_minecraft_whitelist_by_user_and_username(
				reg.get("user_id"),
				reg.get("mc_username"),
			)
			if whitelist_rows:
				whitelist_id = str(whitelist_rows[0].get("id"))
				token = build_integration_removal_token(
					ctx,
					integration_type="minecraft",
					integration_id=whitelist_id,
					user_id=str(reg.get("user_id")),
				)
				cta_label = "Remove integration"
				cta_url = f"{get_public_base_url(ctx).rstrip('/')}/integration/remove?token={token}"
		send_notification_email(
			to_email=reg.get("email"),
			subject="Minecraft whitelist approved",
			title="Minecraft request approved",
			intro=intro,
			details=[
				f"Username: {reg.get('mc_username')}",
			],
			cta_label=cta_label,
			cta_url=cta_url,
		)
		notify_moderators(
			ctx,
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
		user, err = require_admin(ctx)
		if err:
			return err
		reg_id = (flask.request.args.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_minecraft_registration_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
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
					"reviewed_by_user_id": user.get("id"),
				},
				raw_conditions=["id = %s"],
				raw_params=[reg_id],
			)
			anon_user = is_anonymous_user(ctx, reg.get("user_id"))
			cta_label = None
			cta_url = None
			intro = "Your Minecraft whitelist request has been approved."
			if anon_user:
				intro += " This integration was created without a linked account on zubekanov.com."
				whitelist_rows = ctx.interface.get_minecraft_whitelist_by_user_and_username(
					reg.get("user_id"),
					reg.get("mc_username"),
				)
				if whitelist_rows:
					whitelist_id = str(whitelist_rows[0].get("id"))
					token = build_integration_removal_token(
						ctx,
						integration_type="minecraft",
						integration_id=whitelist_id,
						user_id=str(reg.get("user_id")),
					)
					cta_label = "Remove integration"
					cta_url = f"{get_public_base_url(ctx).rstrip('/')}/integration/remove?token={token}"
			send_notification_email(
				to_email=reg.get("email"),
				subject="Minecraft whitelist approved",
				title="Minecraft request approved",
				intro=intro,
				details=[
					f"Username: {reg.get('mc_username')}",
				],
				cta_label=cta_label,
				cta_url=cta_url,
			)
			notify_moderators(
				ctx,
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
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.redirect("/admin/minecraft-approvals")

	@api.route("/api/admin/minecraft/deny", methods=["POST"])
	def api_admin_minecraft_deny():
		user, err = require_admin(ctx)
		if err:
			return err
		data = flask.request.json or {}
		reg_id = (data.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_minecraft_registration_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			updated = ctx.interface.client.update_rows_with_filters(
				"minecraft_registrations",
				{
					"status": "denied",
				},
				raw_conditions=["id = %s"],
				raw_params=[reg_id],
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		if updated == 0:
			return flask.jsonify({"ok": False, "message": "Not found."}), 404
		notify_moderators(
			ctx,
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
		user, err = require_admin(ctx)
		if err:
			return err
		reg_id = (flask.request.args.get("id") or "").strip()
		if not reg_id:
			return flask.jsonify({"ok": False, "message": "Missing id."}), 400
		try:
			rows = ctx.interface.get_minecraft_registration_by_id(reg_id)
			if not rows:
				return flask.jsonify({"ok": False, "message": "Not found."}), 404
			reg = rows[0]
			ctx.interface.client.update_rows_with_filters(
				"minecraft_registrations",
				{
					"status": "denied",
					"reviewed_at": datetime.now(timezone.utc),
					"reviewed_by_user_id": user.get("id"),
				},
				raw_conditions=["id = %s"],
				raw_params=[reg_id],
			)
			notify_moderators(
				ctx,
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
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.redirect("/admin/minecraft-approvals")

	@api.route("/api/admin/minecraft/pending-count")
	def api_admin_minecraft_pending_count():
		_, err = require_admin(ctx)
		if err:
			return err
		try:
			count = ctx.interface.count_pending_minecraft_registrations()
			if count is None:
				raise RuntimeError("Count unavailable")
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400
		return flask.jsonify({"count": count})

	@api.route("/api/admin/minecraft/sync-whitelist", methods=["POST"])
	def api_admin_minecraft_sync_whitelist():
		user, err = require_admin(ctx)
		if err:
			return err

		data = flask.request.json or {}
		dry_run = bool(data.get("dry_run", False))

		try:
			active_rows, _ = ctx.interface.client.get_rows_with_filters(
				"minecraft_whitelist",
				raw_conditions=["COALESCE(is_active, TRUE) = TRUE"],
				page_limit=5000,
				page_num=0,
				order_by="mc_username",
				order_dir="ASC",
			)
			inactive_rows, _ = ctx.interface.client.get_rows_with_filters(
				"minecraft_whitelist",
				raw_conditions=["COALESCE(is_active, FALSE) = FALSE"],
				page_limit=5000,
				page_num=0,
				order_by="mc_username",
				order_dir="ASC",
			)

			active = [(r.get("mc_username") or "").strip() for r in (active_rows or [])]
			inactive = [(r.get("mc_username") or "").strip() for r in (inactive_rows or [])]

			conf = load_amp_minecraft_config()
			client = AmpMinecraftClient(conf)
			result = client.sync_whitelist(
				active_usernames=active,
				inactive_usernames=inactive,
				dry_run=dry_run,
			)

			notify_moderators(
				ctx,
				"minecraft_whitelist_sync",
				title="Minecraft whitelist sync executed",
				actor=user.get("email") or user.get("id"),
				subject=result.get("instance_name") or "Minecraft",
				details=[
					f"Dry run: {bool(result.get('dry_run'))}",
					f"Requested add: {result.get('requested_add', 0)}",
					f"Requested remove: {result.get('requested_remove', 0)}",
					f"Added: {result.get('added', 0)}",
					f"Removed: {result.get('removed', 0)}",
					f"Errors: {len(result.get('errors') or [])}",
				],
				context={
					"action": "minecraft_whitelist_sync",
					"reviewer_user_id": user.get("id"),
					"dry_run": bool(result.get("dry_run")),
					"requested_add": result.get("requested_add", 0),
					"requested_remove": result.get("requested_remove", 0),
					"added": result.get("added", 0),
					"removed": result.get("removed", 0),
				},
			)
		except Exception as e:
			logger.exception("Failed to sync minecraft whitelist to AMP.")
			return flask.jsonify({"ok": False, "message": f"Whitelist sync failed: {e}"}), 400

		return flask.jsonify({"ok": bool(result.get("ok")), "result": result})
