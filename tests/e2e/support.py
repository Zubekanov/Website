from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import bcrypt
from psycopg2.extras import Json


MEMBER_USER_ID = "11111111-1111-1111-1111-111111111111"
ADMIN_USER_ID = "22222222-2222-2222-2222-222222222222"
MEMBER_EMAIL = "member@example.com"
ADMIN_EMAIL = "admin@example.com"
MEMBER_PASSWORD = "MemberPass123!"
ADMIN_PASSWORD = "AdminPass123!"

_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_VERIFY_CODE_RE = re.compile(r"Webhook verification code:\s*([0-9]{6})")


@lru_cache(maxsize=8)
def password_hash(password: str) -> str:
	return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


@dataclass
class E2EState:
	emails: list[dict[str, Any]] = field(default_factory=list)
	webhook_messages: list[dict[str, Any]] = field(default_factory=list)
	webhook_events: list[dict[str, Any]] = field(default_factory=list)
	audiobookshelf_probe_mode: str = "ok"
	audiobookshelf_probe_status: int = 200
	audiobookshelf_probe_error: str = ""

	def reset(self) -> None:
		self.emails.clear()
		self.webhook_messages.clear()
		self.webhook_events.clear()
		self.audiobookshelf_probe_mode = "ok"
		self.audiobookshelf_probe_status = 200
		self.audiobookshelf_probe_error = ""

	def last_email(self) -> dict[str, Any]:
		if not self.emails:
			raise AssertionError("No captured emails were recorded.")
		return self.emails[-1]

	def last_verification_link(self) -> str:
		email = self.last_email()
		for candidate in (email.get("body_text"), email.get("body_html")):
			match = _URL_RE.search(candidate or "")
			if match:
				return match.group(0)
		raise AssertionError("No verification URL found in captured email.")

	def last_webhook_message(self) -> dict[str, Any]:
		if not self.webhook_messages:
			raise AssertionError("No captured webhook messages were recorded.")
		return self.webhook_messages[-1]

	def last_webhook_verification_code(self) -> str:
		payload = self.last_webhook_message().get("payload") or {}
		content = str(payload.get("content") or "")
		match = _VERIFY_CODE_RE.search(content)
		if not match:
			raise AssertionError("No Discord verification code found in captured webhook payload.")
		return match.group(1)

	def set_audiobookshelf_probe_status(self, status_code: int) -> None:
		self.audiobookshelf_probe_mode = "status"
		self.audiobookshelf_probe_status = int(status_code)
		self.audiobookshelf_probe_error = ""

	def set_audiobookshelf_probe_error(self, message: str) -> None:
		self.audiobookshelf_probe_mode = "error"
		self.audiobookshelf_probe_error = message


@dataclass
class SeedHelper:
	interface: Any
	state: E2EState

	def reset_db(self) -> None:
		self.state.reset()
		client = self.interface.client
		rows = client.execute_query(
			"""
			SELECT tablename
			FROM pg_tables
			WHERE schemaname = %s
			ORDER BY tablename;
			""",
			["public"],
		) or []
		table_names = [row["tablename"] for row in rows if row.get("tablename")]
		if not table_names:
			return
		table_refs = ", ".join(f'"public"."{name}"' for name in table_names)
		client.execute_query(f"TRUNCATE TABLE {table_refs} RESTART IDENTITY CASCADE;")

	def seed_baseline(self) -> None:
		client = self.interface.client
		client.insert_row("users", {
			"id": MEMBER_USER_ID,
			"email": MEMBER_EMAIL,
			"first_name": "Member",
			"last_name": "User",
			"password_hash": password_hash(MEMBER_PASSWORD),
			"is_active": True,
			"is_anonymous": False,
		})
		client.insert_row("users", {
			"id": ADMIN_USER_ID,
			"email": ADMIN_EMAIL,
			"first_name": "Admin",
			"last_name": "User",
			"password_hash": password_hash(ADMIN_PASSWORD),
			"is_active": True,
			"is_anonymous": False,
		})
		client.insert_row("admins", {
			"user_id": ADMIN_USER_ID,
			"note": "e2e baseline",
		})
		self._ensure_event_key("test.public", "all", "Public test event")
		self._ensure_event_key("test.user", "users", "Member-only test event")
		self._ensure_event_key("moderator.notifications", "admins", "Moderator notifications")

	def _ensure_event_key(self, event_key: str, permission: str, description: str) -> None:
		self.interface.client.insert_row("discord_event_keys", {
			"event_key": event_key,
			"permission": permission,
			"description": description,
		})

	def create_anonymous_user(
		self,
		*,
		first_name: str = "Anonymous",
		last_name: str = "User",
		email: str | None = None,
	) -> dict[str, Any]:
		user_email = email or f"anon-{uuid.uuid4().hex[:10]}@example.com"
		return self.interface.client.insert_row("users", {
			"email": user_email,
			"first_name": first_name,
			"last_name": last_name,
			"password_hash": None,
			"is_active": True,
			"is_anonymous": True,
		})

	def create_pending_audiobookshelf_request(
		self,
		*,
		user_id: str = MEMBER_USER_ID,
		email: str = MEMBER_EMAIL,
		first_name: str = "Pending",
		last_name: str = "Audio",
		additional_info: str = "E2E audiobookshelf request",
	) -> dict[str, Any]:
		return self.interface.client.insert_row("audiobookshelf_registrations", {
			"user_id": user_id,
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"additional_info": additional_info,
			"status": "pending",
			"is_active": True,
		})

	def create_pending_minecraft_request(
		self,
		*,
		user_id: str = MEMBER_USER_ID,
		email: str = MEMBER_EMAIL,
		first_name: str = "Pending",
		last_name: str = "Minecraft",
		mc_username: str = "E2EPlayer",
		who_are_you: str = "friend",
		additional_info: str = "E2E minecraft request",
	) -> dict[str, Any]:
		return self.interface.client.insert_row("minecraft_registrations", {
			"user_id": user_id,
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"mc_username": mc_username,
			"who_are_you": who_are_you,
			"additional_info": additional_info,
			"status": "pending",
		})

	def create_pending_api_access_request(
		self,
		*,
		user_id: str = MEMBER_USER_ID,
		email: str = MEMBER_EMAIL,
		first_name: str = "Pending",
		last_name: str = "API",
		service_name: str = "E2E Service",
		requested_scopes: list[str] | None = None,
		use_case: str = "E2E API request",
	) -> dict[str, Any]:
		return self.interface.client.insert_row("api_access_registrations", {
			"user_id": user_id,
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"principal_type": "service",
			"service_name": service_name,
			"requested_scopes": Json(requested_scopes or ["metrics.read"]),
			"use_case": use_case,
			"status": "pending",
			"is_active": False,
		})

	def create_pending_discord_webhook_request(
		self,
		*,
		user_id: str = MEMBER_USER_ID,
		name: str = "E2E Webhook",
		webhook_url: str | None = None,
		event_key: str = "test.public",
		submitted_by_email: str = MEMBER_EMAIL,
	) -> dict[str, Any]:
		return self.interface.client.insert_row("discord_webhook_registrations", {
			"name": name,
			"webhook_url": webhook_url or f"https://discord.example/{uuid.uuid4()}",
			"event_key": event_key,
			"submitted_by_user_id": user_id,
			"submitted_by_email": submitted_by_email,
			"status": "pending",
		})

	def create_active_webhook_subscription(
		self,
		*,
		user_id: str = MEMBER_USER_ID,
		name: str = "Profile Webhook",
		webhook_url: str | None = None,
		event_key: str = "test.user",
		subscription_active: bool = True,
		webhook_active: bool = True,
	) -> dict[str, Any]:
		webhook = self.interface.client.insert_row("discord_webhooks", {
			"name": name,
			"user_id": user_id,
			"webhook_url": webhook_url or f"https://discord.example/{uuid.uuid4()}",
			"is_active": webhook_active,
			"updated_at": datetime.now(timezone.utc),
		})
		subscription = self.interface.client.insert_row("discord_webhook_subscriptions", {
			"webhook_id": webhook["id"],
			"event_key": event_key,
			"filter_json": Json({}),
			"format_json": Json({}),
			"is_active": subscription_active,
		})
		return {
			"webhook": webhook,
			"subscription": subscription,
		}

	def create_inactive_webhook_subscription(
		self,
		*,
		user_id: str = MEMBER_USER_ID,
		name: str = "Profile Webhook",
		webhook_url: str | None = None,
		event_key: str = "test.user",
	) -> dict[str, Any]:
		return self.create_active_webhook_subscription(
			user_id=user_id,
			name=name,
			webhook_url=webhook_url,
			event_key=event_key,
			subscription_active=False,
			webhook_active=True,
		)

	def create_minecraft_whitelist_entry(
		self,
		*,
		user_id: str = MEMBER_USER_ID,
		email: str = MEMBER_EMAIL,
		first_name: str = "Member",
		last_name: str = "User",
		mc_username: str = "E2EWhitelist",
		is_active: bool = True,
		ban_reason: str | None = None,
	) -> dict[str, Any]:
		return self.interface.client.insert_row("minecraft_whitelist", {
			"user_id": user_id,
			"first_name": first_name,
			"last_name": last_name,
			"email": email,
			"mc_username": mc_username,
			"is_active": is_active,
			"ban_reason": ban_reason,
			"whitelisted_at": datetime.now(timezone.utc),
		})

	def create_active_audiobookshelf_registration(
		self,
		*,
		user_id: str = MEMBER_USER_ID,
		email: str = MEMBER_EMAIL,
		first_name: str = "Member",
		last_name: str = "User",
		is_active: bool = True,
	) -> dict[str, Any]:
		return self.interface.client.insert_row("audiobookshelf_registrations", {
			"user_id": user_id,
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"status": "approved",
			"is_active": is_active,
			"reviewed_at": datetime.now(timezone.utc),
			"reviewed_by_user_id": ADMIN_USER_ID,
		})

	def build_integration_removal_token(
		self,
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
		sig = hmac.new(self.interface.token_secret(), raw.encode("utf-8"), hashlib.sha256).hexdigest()
		b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8").rstrip("=")
		return f"{b64}.{sig}"
