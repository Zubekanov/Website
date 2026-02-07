from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone, timedelta

import flask
from psycopg2.extras import Json

from app.api_context import ApiContext
from app.api_common import (
	build_admin_action_buttons,
	get_or_create_anonymous_user,
	get_request_user,
	is_anonymous_user,
	notify_moderators,
	parse_integration_removal_token,
	send_notification_email,
)
from util.integrations.discord.webhook_interface import DiscordWebhookEmitter


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/profile/discord-webhook/unsubscribe", methods=["POST"])
	def api_profile_discord_webhook_unsubscribe():
		user = get_request_user(ctx)
		if not user:
			return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401

		data = flask.request.json or {}
		sub_id = (data.get("subscription_id") or data.get("id") or "").strip()
		if not sub_id:
			return flask.jsonify({"ok": False, "message": "Missing subscription id."}), 400

		try:
			rows = ctx.interface.get_discord_subscription_for_user(sub_id, user.get("id"))
			if not rows:
				return flask.jsonify({"ok": False, "message": "Subscription not found."}), 404

			ctx.interface.client.update_rows_with_filters(
				"discord_webhook_subscriptions",
				{"is_active": False},
				raw_conditions=["id = %s"],
				raw_params=[sub_id],
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		return flask.jsonify({"ok": True})

	@api.route("/api/profile/discord-webhook/resubscribe", methods=["POST"])
	def api_profile_discord_webhook_resubscribe():
		user = get_request_user(ctx)
		if not user:
			return flask.jsonify({"ok": False, "message": "Unauthorized."}), 401

		data = flask.request.json or {}
		sub_id = (data.get("subscription_id") or data.get("id") or "").strip()
		if not sub_id:
			return flask.jsonify({"ok": False, "message": "Missing subscription id."}), 400

		try:
			rows = ctx.interface.get_discord_subscription_with_webhook_active(sub_id, user.get("id"))
			if not rows:
				return flask.jsonify({"ok": False, "message": "Subscription not found."}), 404
			if not rows[0].get("webhook_active", True):
				return flask.jsonify({"ok": False, "message": "Webhook is inactive. Reactivate it first."}), 403

			ctx.interface.client.update_rows_with_filters(
				"discord_webhook_subscriptions",
				{"is_active": True},
				raw_conditions=["id = %s"],
				raw_params=[sub_id],
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		return flask.jsonify({"ok": True})

	@api.route("/api/profile/integration/delete", methods=["POST"])
	def api_profile_integration_delete():
		user = get_request_user(ctx)
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
				return _disable_discord_webhook_for_user(ctx, user, integration_id, reason)
			if integration_type == "minecraft":
				return _disable_minecraft_for_user(ctx, user, integration_id, reason)
			if integration_type == "audiobookshelf":
				return _disable_audiobookshelf_for_user(ctx, user, integration_id, reason)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		return flask.jsonify({"ok": False, "message": "Unknown integration type."}), 400

	@api.route("/audiobookshelf-registration", methods=["POST"])
	def api_audiobookshelf_registration():
		data = flask.request.json or {}
		first_name = (data.get("first_name") or "").strip()
		last_name = (data.get("last_name") or "").strip()
		email = (data.get("email") or "").strip().lower()
		additional_info = (data.get("additional_info") or "").strip()
		user_id = None
		user = get_request_user(ctx)
		if user:
			user_id = user.get("id")
		is_admin = _is_admin(ctx, user_id)

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
			ok, anon_id = get_or_create_anonymous_user(
				ctx,
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
			row = ctx.interface.client.insert_row("audiobookshelf_registrations", {
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
				ctx.interface.client.delete_rows_with_filters(
					"application_exemptions",
					raw_conditions=["user_id = %s", "integration_type = 'audiobookshelf'"],
					raw_params=[user_id],
				)
			if is_admin:
				notify_moderators(
					ctx,
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
				notify_moderators(
					ctx,
					"audiobookshelf_request_submitted",
					title="New audiobookshelf request",
					actor=user.get("email") if user else "Anonymous",
					subject=f"{first_name} {last_name}".strip() or email,
					details=[
						f"Email: {email}" if email else "",
						f"User ID: {user_id}" if user_id else "User ID: anonymous",
						f"Additional info: {additional_info}" if additional_info else "",
					],
					buttons=build_admin_action_buttons("audiobookshelf", str(row["id"])),
					context={
						"action": "audiobookshelf_request_submitted",
						"user_id": user_id,
						"email": email,
					},
				)
		except Exception as e:
			return flask.jsonify({
				"ok": False,
				"message": "Request failed. Please try again.",
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
		user = get_request_user(ctx)
		if user:
			user_id = user.get("id")
		is_admin = _is_admin(ctx, user_id)

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
			ok, anon_id = get_or_create_anonymous_user(
				ctx,
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
				exemption = ctx.interface.get_application_exemption_with_key(
					user_id,
					"minecraft",
					mc_username,
				)
			existing = ctx.interface.get_minecraft_registration_by_username(mc_username)
			if existing and not exemption:
				return flask.jsonify({
					"ok": False,
					"message": "That Minecraft username already has an application on file.",
				}), 400
			if exemption:
				ctx.interface.client.delete_rows_with_filters(
					"application_exemptions",
					raw_conditions=["id = %s"],
					raw_params=[exemption[0]["id"]],
				)
				ctx.interface.client.delete_rows_with_filters(
					"minecraft_registrations",
					raw_conditions=["LOWER(mc_username) = LOWER(%s)"],
					raw_params=[mc_username],
				)
			whitelisted = ctx.interface.get_minecraft_whitelist_active_by_username(mc_username)
			if whitelisted:
				return flask.jsonify({
					"ok": False,
					"message": "That Minecraft username is already whitelisted.",
				}), 400
			row = ctx.interface.client.insert_row("minecraft_registrations", {
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
				existing = ctx.interface.get_minecraft_whitelist_by_username(mc_username)
				if not existing:
					ctx.interface.client.insert_row("minecraft_whitelist", {
						"user_id": user_id,
						"first_name": first_name,
						"last_name": last_name,
						"email": email,
						"mc_username": mc_username,
						"is_active": True,
					})
				else:
					ctx.interface.client.update_rows_with_filters(
						"minecraft_whitelist",
						{"is_active": True, "ban_reason": None},
						raw_conditions=["id = %s"],
						raw_params=[existing[0]["id"]],
					)
				notify_moderators(
					ctx,
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
				notify_moderators(
					ctx,
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
					buttons=build_admin_action_buttons("minecraft", str(row["id"])),
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
				"message": "Request failed. Please try again.",
			}), 400

		return flask.jsonify({
			"ok": True,
			"message": "Request approved." if is_admin else "Request submitted. You will receive a follow-up email if approved.",
		})

	@api.route("/api-access-application", methods=["POST"])
	def api_api_access_application():
		data = flask.request.json or {}
		first_name = (data.get("first_name") or "").strip()
		last_name = (data.get("last_name") or "").strip()
		email = (data.get("email") or "").strip().lower()
		principal_type = (data.get("principal_type") or "service").strip().lower()
		service_name = (data.get("service_name") or "").strip()
		requested_scopes_raw = (data.get("requested_scopes") or "").strip()
		use_case = (data.get("use_case") or "").strip()

		user_id = None
		user = get_request_user(ctx)
		if user:
			user_id = user.get("id")
		is_admin = _is_admin(ctx, user_id)

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

		if "@" not in email:
			return flask.jsonify({
				"ok": False,
				"message": "Invalid email address.",
			}), 400

		if principal_type not in {"user", "service"}:
			return flask.jsonify({"ok": False, "message": "Invalid principal type."}), 400
		if principal_type == "service" and not service_name:
			return flask.jsonify({"ok": False, "message": "Service name is required for service principals."}), 400
		if not user:
			ok, anon_id = get_or_create_anonymous_user(
				ctx,
				first_name=first_name,
				last_name=last_name,
				email=email,
			)
			if not ok:
				return flask.jsonify({"ok": False, "message": anon_id}), 400
			user_id = anon_id

		requested_scopes = sorted({
			scope.strip().lower()
			for scope in requested_scopes_raw.split(",")
			if scope and scope.strip()
		})
		if not requested_scopes:
			return flask.jsonify({"ok": False, "message": "At least one requested scope is required."}), 400
		if len(requested_scopes) > 50:
			return flask.jsonify({"ok": False, "message": "Too many scopes requested."}), 400
		if not use_case:
			return flask.jsonify({"ok": False, "message": "Use case is required."}), 400

		try:
			row = ctx.interface.client.insert_row("api_access_registrations", {
				"first_name": first_name,
				"last_name": last_name,
				"email": email,
				"user_id": user_id,
				"principal_type": principal_type,
				"service_name": service_name or None,
				"requested_scopes": Json(requested_scopes),
				"use_case": use_case,
				"status": "approved" if is_admin else "pending",
				"is_active": True if is_admin else False,
				"reviewed_at": datetime.now(timezone.utc) if is_admin else None,
				"reviewed_by_user_id": user_id if is_admin else None,
			})
			if is_admin:
				notify_moderators(
					ctx,
					"api_access_request_approved",
					title="API access request auto-approved",
					actor=user.get("email") if user else "Admin",
					subject=service_name or email,
					details=[
						f"Email: {email}",
						f"Principal: {principal_type}",
						f"Service: {service_name}" if service_name else "",
						f"Scopes: {', '.join(requested_scopes)}",
						f"Use case: {use_case}",
						"Auto-approved (admin request).",
						f"Request ID: {row['id']}",
					],
					context={
						"action": "api_access_request_approved",
						"user_id": user_id,
						"email": email,
						"reviewer_user_id": user_id,
						"request_id": str(row["id"]),
					},
				)
			else:
				notify_moderators(
					ctx,
					"api_access_request_submitted",
					title="New API access request",
					actor=user.get("email") if user else "Anonymous",
					subject=service_name or email,
					details=[
						f"Email: {email}",
						f"Principal: {principal_type}",
						f"Service: {service_name}" if service_name else "",
						f"Scopes: {', '.join(requested_scopes)}",
						f"Use case: {use_case}",
					],
					buttons=build_admin_action_buttons("api-access", str(row["id"])),
					context={
						"action": "api_access_request_submitted",
						"user_id": user_id,
						"email": email,
						"request_id": str(row["id"]),
					},
				)
		except Exception as e:
			return flask.jsonify({
				"ok": False,
				"message": "Request failed. Please try again.",
			}), 400

		return flask.jsonify({
			"ok": True,
			"message": "Application approved." if is_admin else "Application submitted. You will receive a follow-up email if approved.",
		})

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

		user = get_request_user(ctx)
		is_admin = bool(user and ctx.interface.is_admin(user.get("id")))
		user_id = user.get("id") if user else None

		allowed_permissions = ["all"]
		if user:
			allowed_permissions.append("users")
		if is_admin:
			allowed_permissions.append("admins")

		rows, _ = ctx.interface.client.get_rows_with_filters(
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
		exemption = None
		existing_webhooks = []
		try:
			if user_id:
				exemption = ctx.interface.get_application_exemption(user_id, "discord_webhook")
			existing_sub = ctx.interface.get_discord_subscription_by_webhook_url_event_key(webhook_url, event_key)
			if existing_sub:
				sub = existing_sub[0]
				if not bool(sub.get("webhook_active", True)):
					return flask.jsonify({
						"ok": False,
						"message": "That webhook is inactive and cannot be re-verified. Contact an admin.",
					}), 403
				if not bool(sub.get("is_active", True)):
					ctx.interface.client.update_rows_with_filters(
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
					ctx.interface.client.delete_rows_with_filters(
						"application_exemptions",
						raw_conditions=["id = %s"],
						raw_params=[exemption[0]["id"]],
					)
					ctx.interface.client.delete_rows_with_filters(
						"discord_webhook_registrations",
						raw_conditions=["webhook_url = %s", "event_key = %s"],
						raw_params=[webhook_url, event_key],
					)
				else:
					return flask.jsonify({
						"ok": False,
						"message": "That webhook is already subscribed to this event key.",
					}), 400

			existing_webhooks = ctx.interface.get_discord_webhook_by_url(webhook_url)
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
				"message": "Request failed. Please try again.",
			}), 400
		try:
			existing = ctx.interface.get_discord_webhook_registration_by_url_event_key(webhook_url, event_key)
			if existing and not exemption:
				return flask.jsonify({
					"ok": False,
					"message": "That webhook URL is already registered for this event key.",
				}), 400
		except Exception as e:
			return flask.jsonify({
				"ok": False,
				"message": "Request failed. Please try again.",
			}), 400

		if existing_webhooks:
			if not user_id:
				if not first_name or not last_name or not contact_email:
					return flask.jsonify({
						"ok": False,
						"message": "First name, last name, and contact email are required.",
					}), 400
			try:
				row = ctx.interface.client.insert_row("discord_webhook_registrations", {
					"name": name,
					"webhook_url": webhook_url,
					"event_key": event_key,
					"submitted_by_user_id": user_id,
					"submitted_by_name": f"{first_name} {last_name}".strip() if not user_id else None,
					"submitted_by_email": contact_email if not user_id else None,
					"status": "pending",
				})
				if is_admin:
					emitter = DiscordWebhookEmitter(ctx.interface)
					ok, msg = emitter.approve_registration(
						registration_id=str(row["id"]),
						reviewer_user_id=user_id,
					)
					if not ok:
						return flask.jsonify({"ok": False, "message": msg}), 400
					notify_moderators(
						ctx,
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
					notify_moderators(
						ctx,
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
						buttons=build_admin_action_buttons("discord-webhook", str(row["id"])),
						context={
							"action": "discord_webhook_request_submitted",
							"user_id": user_id,
							"event_key": event_key,
						},
					)
			except Exception as e:
				return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

			return flask.jsonify({
				"ok": True,
				"message": "Request approved." if is_admin else "Request submitted.",
				"redirect": "/discord-webhook/verified?status=approved" if is_admin else "/discord-webhook/verified?status=submitted",
			})

		code = f"{secrets.randbelow(1000000):06d}"
		verify_id = None
		# Store verification first to include a link in the message.
		secret = ctx.interface._token_secret()
		code_hash = hmac.new(secret, code.encode("utf-8"), hashlib.sha256).hexdigest()
		expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
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
			ok, anon_id = get_or_create_anonymous_user(
				ctx,
				first_name=first_name,
				last_name=last_name,
				email=contact_email,
			)
			if not ok:
				return flask.jsonify({"ok": False, "message": anon_id}), 400
			user_id = anon_id

		try:
			ctx.interface.client.delete_rows_with_filters(
				"discord_webhook_verifications",
				raw_conditions=["webhook_url = %s", "event_key = %s"],
				raw_params=[webhook_url, event_key],
			)
			row = ctx.interface.client.insert_row("discord_webhook_verifications", {
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
				"message": "Request failed. Please try again.",
			}), 400

		verify_link = f"{flask.request.host_url.rstrip('/')}/token?vid={verify_id}&code={code}"
		payload = {
			"content": (
				f"Webhook verification code: {code}\n"
				f"This verifies the webhook for event key: {event_key}\n"
				f"Submit code: {verify_link}"
			)
		}

		emitter = DiscordWebhookEmitter(ctx.interface)
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

		rows, _ = ctx.interface.client.get_rows_with_filters(
			"discord_webhook_verifications",
			raw_conditions=["id = %s", "expires_at >= NOW()"],
			raw_params=[verify_id],
			page_limit=1,
			page_num=0,
		)
		if not rows:
			return flask.jsonify({"ok": False, "message": "Verification expired or invalid."}), 400
		ver = rows[0]

		secret = ctx.interface._token_secret()
		code_hash = hmac.new(secret, code.encode("utf-8"), hashlib.sha256).hexdigest()
		if code_hash != ver.get("code_hash"):
			return flask.jsonify({"ok": False, "message": "Invalid verification code."}), 400

		user = get_request_user(ctx)
		user_id = ver.get("requested_by_user_id") or (user.get("id") if user else None)
		is_admin = _is_admin(ctx, user_id)
		try:
			exemption = None
			if user_id:
				exemption = ctx.interface.get_application_exemption(user_id, "discord_webhook")
				if exemption:
					ctx.interface.client.delete_rows_with_filters(
						"application_exemptions",
						raw_conditions=["id = %s"],
						raw_params=[exemption[0]["id"]],
					)
					ctx.interface.client.delete_rows_with_filters(
						"discord_webhook_registrations",
						raw_conditions=["webhook_url = %s", "event_key = %s"],
						raw_params=[ver["webhook_url"], ver["event_key"]],
					)
			existing_sub = ctx.interface.get_discord_subscription_by_webhook_url_event_key(
				ver["webhook_url"],
				ver["event_key"],
			)
			if existing_sub:
				sub = existing_sub[0]
				if not bool(sub.get("webhook_active", True)):
					return flask.jsonify({
						"ok": False,
						"message": "That webhook is inactive and cannot be re-verified. Contact an admin.",
					}), 403
				if not bool(sub.get("is_active", True)):
					ctx.interface.client.update_rows_with_filters(
						"discord_webhook_subscriptions",
						{"is_active": True},
						raw_conditions=["id = %s"],
						raw_params=[sub["id"]],
					)
					ctx.interface.client.delete_rows_with_filters(
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

			existing_webhooks = ctx.interface.get_discord_webhook_by_url(ver["webhook_url"])
			if existing_webhooks and not bool(existing_webhooks[0].get("is_active", True)):
				return flask.jsonify({
					"ok": False,
					"message": "That webhook is inactive and cannot be re-verified. Contact an admin.",
				}), 403

			existing = ctx.interface.get_discord_webhook_registration_by_url_event_key(
				ver["webhook_url"],
				ver["event_key"],
			)
			if existing and not exemption:
				return flask.jsonify({
					"ok": False,
					"message": "That webhook URL is already registered for this event key.",
				}), 400
			row = ctx.interface.client.insert_row("discord_webhook_registrations", {
				"name": ver["name"],
				"webhook_url": ver["webhook_url"],
				"event_key": ver["event_key"],
				"submitted_by_user_id": user_id,
				"submitted_by_name": ver.get("contact_name"),
				"submitted_by_email": ver.get("contact_email"),
				"status": "pending",
			})
			if is_admin:
				emitter = DiscordWebhookEmitter(ctx.interface)
				ok, msg = emitter.approve_registration(
					registration_id=str(row["id"]),
					reviewer_user_id=user_id,
				)
				if not ok:
					return flask.jsonify({"ok": False, "message": msg}), 400
				notify_moderators(
					ctx,
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
				notify_moderators(
					ctx,
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
					buttons=build_admin_action_buttons("discord-webhook", str(row["id"])),
					context={
						"action": "discord_webhook_request_submitted",
						"user_id": user_id,
						"event_key": ver.get("event_key"),
					},
				)
			ctx.interface.client.delete_rows_with_filters(
				"discord_webhook_verifications",
				raw_conditions=["id = %s"],
				raw_params=[verify_id],
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		return flask.jsonify({
			"ok": True,
			"redirect": "/discord-webhook/verified?status=approved" if is_admin else "/discord-webhook/verified?status=submitted",
			"message": "Request approved." if is_admin else "Request submitted.",
		})

	@api.route("/api/integration/remove", methods=["POST"])
	def api_integration_remove():
		data = flask.request.json or {}
		token = (data.get("token") or "").strip()
		if not token:
			return flask.jsonify({"ok": False, "message": "Missing token."}), 400
		payload = parse_integration_removal_token(ctx, token)
		if not payload:
			return flask.jsonify({"ok": False, "message": "Invalid or expired token."}), 400

		integration_type = str(payload.get("type") or "").strip()
		integration_id = str(payload.get("id") or "").strip()
		user_id = str(payload.get("user") or "").strip()
		if not integration_type or not integration_id or not user_id:
			return flask.jsonify({"ok": False, "message": "Invalid token payload."}), 400
		is_anon = is_anonymous_user(ctx, user_id)

		try:
			if integration_type == "discord_webhook":
				rows = ctx.interface.get_discord_webhook_for_user(integration_id, user_id)
				if not rows:
					return flask.jsonify({"ok": False, "message": "Webhook not found."}), 404
				ctx.interface.client.update_rows_with_filters(
					"discord_webhooks",
					{"is_active": False},
					raw_conditions=["id = %s"],
					raw_params=[integration_id],
				)
				if is_anon:
					notify_moderators(
						ctx,
						"integration_removed",
						title="Anonymous integration removed",
						actor="Anonymous",
						subject="Discord Webhook",
						details=[
							f"Integration ID: {integration_id}",
							f"User ID: {user_id}",
						],
						context={
							"action": "integration_removed",
							"integration_type": "discord_webhook",
							"integration_id": integration_id,
							"user_id": user_id,
							"source": "email_removal",
						},
					)
				return flask.jsonify({
					"ok": True,
					"message": "Webhook removed.",
					"redirect": "/integration/removed",
				})
			if integration_type == "minecraft":
				rows = ctx.interface.get_minecraft_whitelist_entry_for_user(integration_id, user_id)
				if not rows:
					return flask.jsonify({"ok": False, "message": "Minecraft whitelist entry not found."}), 404
				ctx.interface.client.update_rows_with_filters(
					"minecraft_whitelist",
					{"is_active": False},
					raw_conditions=["id = %s"],
					raw_params=[integration_id],
				)
				if is_anon:
					notify_moderators(
						ctx,
						"integration_removed",
						title="Anonymous integration removed",
						actor="Anonymous",
						subject="Minecraft",
						details=[
							f"Integration ID: {integration_id}",
							f"User ID: {user_id}",
						],
						context={
							"action": "integration_removed",
							"integration_type": "minecraft",
							"integration_id": integration_id,
							"user_id": user_id,
							"source": "email_removal",
						},
					)
				return flask.jsonify({
					"ok": True,
					"message": "Minecraft integration removed.",
					"redirect": "/integration/removed",
				})
			if integration_type == "audiobookshelf":
				rows = ctx.interface.get_audiobookshelf_registration_for_user(integration_id, user_id)
				if not rows:
					return flask.jsonify({"ok": False, "message": "Audiobookshelf registration not found."}), 404
				ctx.interface.client.update_rows_with_filters(
					"audiobookshelf_registrations",
					{"is_active": False},
					raw_conditions=["id = %s"],
					raw_params=[integration_id],
				)
				if is_anon:
					notify_moderators(
						ctx,
						"integration_removed",
						title="Anonymous integration removed",
						actor="Anonymous",
						subject="Audiobookshelf",
						details=[
							f"Integration ID: {integration_id}",
							f"User ID: {user_id}",
						],
						context={
							"action": "integration_removed",
							"integration_type": "audiobookshelf",
							"integration_id": integration_id,
							"user_id": user_id,
							"source": "email_removal",
						},
					)
				return flask.jsonify({
					"ok": True,
					"message": "Audiobookshelf integration removed.",
					"redirect": "/integration/removed",
				})
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 400

		return flask.jsonify({"ok": False, "message": "Unknown integration type."}), 400


def _is_admin(ctx: ApiContext, user_id: str | None) -> bool:
	if not user_id:
		return False
	try:
		return ctx.interface.is_admin(user_id)
	except Exception:
		return False


def _disable_discord_webhook_for_user(ctx: ApiContext, user: dict, integration_id: str, reason: str):
	rows = ctx.interface.get_discord_webhook_for_user(integration_id, user.get("id"))
	if not rows:
		return flask.jsonify({"ok": False, "message": "Webhook not found."}), 404
	ctx.interface.client.update_rows_with_filters(
		"discord_webhooks",
		{"is_active": False},
		raw_conditions=["id = %s"],
		raw_params=[integration_id],
	)
	ctx.interface.client.delete_rows_with_filters(
		"application_exemptions",
		raw_conditions=["user_id = %s", "integration_type = 'discord_webhook'"],
		raw_params=[user.get("id")],
	)
	ctx.interface.client.insert_row("application_exemptions", {
		"user_id": user.get("id"),
		"integration_type": "discord_webhook",
		"integration_key": None,
	})
	send_notification_email(
		to_email=user.get("email"),
		subject="Discord webhook disabled",
		title="Discord webhook disabled",
		intro="Your Discord webhook integration has been disabled.",
		details=[
			f"Reason: {reason}",
		],
	)
	notify_moderators(
		ctx,
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


def _disable_minecraft_for_user(ctx: ApiContext, user: dict, integration_id: str, reason: str):
	rows = ctx.interface.get_minecraft_whitelist_entry_for_user(integration_id, user.get("id"))
	if not rows:
		return flask.jsonify({"ok": False, "message": "Minecraft whitelist entry not found."}), 404
	mc_rows = ctx.interface.get_minecraft_whitelist_username_by_id(integration_id)
	mc_username = mc_rows[0].get("mc_username") if mc_rows else None
	existing_reason = (rows[0].get("ban_reason") or "").strip()
	note = "Account whitelisting disabled from user profile; enable by reapplying."
	combined_reason = existing_reason
	if note not in existing_reason:
		combined_reason = (existing_reason + "\n" + note).strip() if existing_reason else note
	ctx.interface.client.update_rows_with_filters(
		"minecraft_whitelist",
		{"is_active": False, "ban_reason": combined_reason},
		raw_conditions=["id = %s"],
		raw_params=[integration_id],
	)
	ctx.interface.client.delete_rows_with_filters(
		"application_exemptions",
		raw_conditions=["user_id = %s", "integration_type = 'minecraft'", "integration_key = %s"],
		raw_params=[user.get("id"), mc_username],
	)
	ctx.interface.client.insert_row("application_exemptions", {
		"user_id": user.get("id"),
		"integration_type": "minecraft",
		"integration_key": mc_username,
	})
	send_notification_email(
		to_email=user.get("email"),
		subject="Minecraft integration disabled",
		title="Minecraft integration disabled",
		intro="Your Minecraft whitelist integration has been disabled.",
		details=[
			f"Username: {mc_username}" if mc_username else "",
			f"Reason: {reason}",
		],
	)
	notify_moderators(
		ctx,
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


def _disable_audiobookshelf_for_user(ctx: ApiContext, user: dict, integration_id: str, reason: str):
	rows = ctx.interface.get_audiobookshelf_registration_for_user(integration_id, user.get("id"))
	if not rows:
		return flask.jsonify({"ok": False, "message": "Audiobookshelf integration not found."}), 404
	ctx.interface.client.update_rows_with_filters(
		"audiobookshelf_registrations",
		{"is_active": False},
		raw_conditions=["id = %s"],
		raw_params=[integration_id],
	)
	ctx.interface.client.delete_rows_with_filters(
		"application_exemptions",
		raw_conditions=["user_id = %s", "integration_type = 'audiobookshelf'"],
		raw_params=[user.get("id")],
	)
	ctx.interface.client.insert_row("application_exemptions", {
		"user_id": user.get("id"),
		"integration_type": "audiobookshelf",
		"integration_key": None,
	})
	send_notification_email(
		to_email=user.get("email"),
		subject="Audiobookshelf integration disabled",
		title="Audiobookshelf integration disabled",
		intro="Your Audiobookshelf integration has been disabled.",
		details=[
			f"Reason: {reason}",
		],
	)
	notify_moderators(
		ctx,
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
