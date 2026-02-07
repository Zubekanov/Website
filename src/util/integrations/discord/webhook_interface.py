from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from sql.psql_interface import PSQLInterface

logger = logging.getLogger(__name__)


def ensure_event_keys(interface: PSQLInterface, json_path: str | None = None) -> None:
	"""
	Ensure event keys from a JSON definition exist in discord_event_keys.
	JSON format: [{"event_key": "...", "permission": "admins", "description": "..."}]
	"""
	path = json_path or os.path.join(os.path.dirname(__file__), "event_keys.json")
	try:
		with open(path, "r", encoding="utf-8") as handle:
			payload = json.load(handle)
	except Exception as e:
		logger.warning("Failed to read event keys json: %s", e)
		return

	if not isinstance(payload, list):
		logger.warning("Event keys json must be a list of objects.")
		return

	for item in payload:
		if not isinstance(item, dict):
			continue
		event_key = (item.get("event_key") or "").strip()
		permission = (item.get("permission") or "").strip()
		description = (item.get("description") or None)
		if not event_key or not permission:
			continue
		try:
			rows, _ = interface.client.get_rows_with_filters(
				"discord_event_keys",
				equalities={"event_key": event_key},
				page_limit=1,
				page_num=0,
			)
			if rows:
				interface.client.update_rows_with_equalities(
					"discord_event_keys",
					{
						"permission": permission,
						"description": description,
					},
					{"event_key": event_key},
				)
			else:
				interface.client.insert_row("discord_event_keys", {
					"event_key": event_key,
					"permission": permission,
					"description": description,
				})
		except Exception as e:
			logger.warning("Failed to ensure event key %s: %s", event_key, e)


@dataclass(frozen=True)
class DiscordSendResult:
	ok: bool
	status_code: int | None
	error: str | None = None


class DiscordWebhookEmitter:
	"""
	Event emitter:

		emit_event("server_metrics.alert", payload={...}, context={...})

	DB schema assumed:
	- discord_webhooks
	- discord_webhook_subscriptions
	"""

	def __init__(self, interface: PSQLInterface, *, timeout_s: float = 8.0, avatar_url: str | None = None, username: str | None = None):
		self._interface = interface
		self._client = interface.client
		self._timeout_s = float(timeout_s)
		self._verify_timeout_s = float(timeout_s)
		self._default_username = username or "zubekanov.com"
		self._default_avatar_url = avatar_url or "https://zubekanov.com/static/favicon/android-chrome-192x192.png"

	def list_event_keys(self, permissions: list[str] | None = None) -> list[dict[str, Any]]:
		"""
		Return event key rows from discord_event_keys.
		If permissions is provided, only rows with permission in that list are returned.
		"""
		if permissions:
			rows, _ = self._client.get_rows_with_filters(
				"discord_event_keys",
				raw_conditions=["permission = ANY(%s)"],
				raw_params=[permissions],
				page_limit=1000,
				page_num=0,
				order_by="event_key",
				order_dir="ASC",
			)
			return rows or []

		rows, _ = self._client.get_rows_with_filters(
			"discord_event_keys",
			page_limit=1000,
			page_num=0,
			order_by="event_key",
			order_dir="ASC",
		)
		return rows or []

	def event_key_exists(self, event_key: str) -> bool:
		if not event_key or not str(event_key).strip():
			return False
		rows, _ = self._client.get_rows_with_filters(
			"discord_event_keys",
			equalities={"event_key": event_key.strip()},
			page_limit=1,
			page_num=0,
		)
		return bool(rows)

	def add_webhook(
		self,
		*,
		name: str,
		webhook_url: str,
		user_id: str | None = None,
		guild_id: str | None = None,
		channel_id: str | None = None,
		is_active: bool = True,
	) -> tuple[bool, str]:
		if not name or not name.strip():
			return False, "name is required."
		if not webhook_url or not webhook_url.strip():
			return False, "webhook_url is required."

		now = datetime.now(timezone.utc)
		url = webhook_url.strip()

		existing = self._client.get_rows_with_filters(
			"discord_webhooks",
			equalities={"webhook_url": url},
			page_limit=1,
			page_num=0,
		)[0]

		try:
			if existing:
				webhook_id = str(existing[0]["id"])
				self._client.update_rows_with_equalities(
					"discord_webhooks",
					{
						"name": name.strip(),
						"user_id": user_id,
						"guild_id": guild_id,
						"channel_id": channel_id,
						"is_active": bool(is_active),
						"updated_at": now,
					},
					{"id": webhook_id},
				)
				return True, webhook_id

			row = {
				"name": name.strip(),
				"webhook_url": url,
				"user_id": user_id,
				"guild_id": guild_id,
				"channel_id": channel_id,
				"is_active": bool(is_active),
				"created_at": now,
				"updated_at": now,
			}
			ins = self._client.insert_row("discord_webhooks", row)
			return True, str(ins["id"])
		except Exception as e:
			return False, "Unable to save webhook."

	def subscribe_webhook_to_event(
		self,
		*,
		webhook_id: str,
		event_key: str,
		filter_json: dict[str, Any] | None = None,
		format_json: dict[str, Any] | None = None,
		is_active: bool = True,
		allow_multiple_filters: bool = True,
		validate_event_key: bool = True,
	) -> tuple[bool, str]:
		"""
		Subscribe a webhook to an event type.

		- event_key: the "class" of message (topic), eg "server_metrics.alert"
		- filter_json: matching rules against the emitted context (optional)
		- format_json: Discord webhook options merged into payload (optional)

		If allow_multiple_filters=True, we always INSERT a new row (supports multiple distinct filters).
		If allow_multiple_filters=False, we UPSERT on (webhook_id, event_key) (single subscription per event).
		"""
		if not webhook_id or not str(webhook_id).strip():
			return False, "webhook_id is required."
		if not event_key or not str(event_key).strip():
			return False, "event_key is required."

		event_key = event_key.strip()
		if validate_event_key and not self.event_key_exists(event_key):
			return False, "Unknown event_key."
		filter_json = filter_json or {}
		format_json = format_json or {}
		now = datetime.now(timezone.utc)

		webhooks = self._client.get_rows_with_filters(
			"discord_webhooks",
			equalities={"id": webhook_id},
			page_limit=1,
			page_num=0,
		)[0]
		if not webhooks:
			return False, "Webhook not found."
		if not webhooks[0].get("is_active", True):
			return False, "Webhook is inactive."

		try:
			if not allow_multiple_filters:
				existing = self._client.get_rows_with_filters(
					"discord_webhook_subscriptions",
					equalities={"webhook_id": webhook_id, "event_key": event_key},
					page_limit=1,
					page_num=0,
				)[0]
				if existing:
					sub_id = str(existing[0]["id"])
					self._client.update_rows_with_equalities(
						"discord_webhook_subscriptions",
						{
							"filter_json": json.dumps(filter_json),
							"format_json": json.dumps(format_json),
							"is_active": bool(is_active),
						},
						{"id": sub_id},
					)
					return True, sub_id

			row = {
				"webhook_id": webhook_id,
				"event_key": event_key,
				"filter_json": json.dumps(filter_json),
				"format_json": json.dumps(format_json),
				"is_active": bool(is_active),
				"created_at": now,
			}
			ins = self._client.insert_row("discord_webhook_subscriptions", row)
			return True, str(ins["id"])
		except Exception as e:
			return False, "Unable to save subscription."
		
	def emit_event(
		self,
		event_key: str,
		*,
		payload: dict[str, Any],
		context: dict[str, Any] | None = None,
	) -> list[tuple[str, DiscordSendResult]]:
		"""
		Emit an event to all matching webhook subscriptions.

		Matching logic:
		- subscription.event_key == event_key
		- subscription.is_active == true
		- webhook.is_active == true
		- filter_json matches the emitted context (if filter_json non-empty)
		"""
		if not event_key or not str(event_key).strip():
			raise ValueError("event_key is required.")
		event_key = event_key.strip()

		ctx = context or {}

		sub_rows, _ = self._client.get_rows_with_filters(
			"discord_webhook_subscriptions",
			equalities={"event_key": event_key, "is_active": True},
			page_limit=1000,
			page_num=0,
		)

		if not sub_rows:
			return []

		results: list[tuple[str, DiscordSendResult]] = []

		webhook_cache: dict[str, dict[str, Any] | None] = {}

		for sub in sub_rows:
			webhook_id = str(sub.get("webhook_id") or "")
			if not webhook_id:
				continue

			sub_filter = self._as_dict(sub.get("filter_json"))
			if sub_filter and not self._filter_matches(sub_filter, ctx):
				continue

			if webhook_id not in webhook_cache:
				wh = self._client.get_rows_with_filters(
					"discord_webhooks",
					equalities={"id": webhook_id},
					page_limit=1,
					page_num=0,
				)[0]
				webhook_cache[webhook_id] = wh[0] if wh else None

			webhook = webhook_cache[webhook_id]
			if not webhook:
				continue
			if not webhook.get("is_active", True):
				continue

			final_payload = self._apply_format_json(payload, sub.get("format_json"))
			res = self._send_and_record(webhook, final_payload)
			results.append((webhook_id, res))

		return results

	def approve_registration(
		self,
		*,
		registration_id: str,
		reviewer_user_id: str | None,
	) -> tuple[bool, str]:
		if not registration_id:
			return False, "registration_id is required."

		rows, _ = self._client.get_rows_with_filters(
			"discord_webhook_registrations",
			raw_conditions=["id = %s"],
			raw_params=[registration_id],
			page_limit=1,
			page_num=0,
		)
		if not rows:
			return False, "Registration not found."
		reg = rows[0]
		if reg.get("status") == "approved":
			return True, "Already approved."

		ok, webhook_id = self.add_webhook(
			name=reg["name"],
			webhook_url=reg["webhook_url"],
			user_id=reg.get("submitted_by_user_id"),
			guild_id=None,
			channel_id=None,
			is_active=True,
		)
		if not ok:
			return False, webhook_id

		ok, sub_id = self.subscribe_webhook_to_event(
			webhook_id=webhook_id,
			event_key=reg["event_key"],
			filter_json={},
			format_json={},
			is_active=True,
			allow_multiple_filters=True,
		)
		if not ok:
			return False, sub_id

		self._client.update_rows_with_filters(
			"discord_webhook_registrations",
			{
				"status": "approved",
				"reviewed_at": datetime.now(timezone.utc),
				"reviewed_by_user_id": reviewer_user_id,
			},
			raw_conditions=["id = %s"],
			raw_params=[registration_id],
		)

		# Notify submitter webhook on approval.
		approval_payload = {
			"content": f"Your webhook registration for event key '{reg['event_key']}' has been approved."
		}
		self.send_test_message(reg["webhook_url"], approval_payload)

		return True, "Approved."

	@staticmethod
	def _as_dict(v: Any) -> dict[str, Any]:
		if v is None:
			return {}
		if isinstance(v, dict):
			return v
		if isinstance(v, str) and v.strip():
			try:
				obj = json.loads(v)
				return obj if isinstance(obj, dict) else {}
			except Exception:
				return {}
		return {}

	@staticmethod
	def _filter_matches(filter_json: dict[str, Any], context: dict[str, Any]) -> bool:
		"""
		Rule:
		- For each key in filter_json:
			- if filter value is list: context value must be in list
			- else: context value must equal filter value
		"""
		for k, want in filter_json.items():
			if k not in context:
				return False
			got = context.get(k)

			if isinstance(want, list):
				if got not in want:
					return False
			else:
				if got != want:
					return False

		return True

	def _apply_format_json(self, payload: dict[str, Any], format_json: Any) -> dict[str, Any]:
		"""
		format_json is merged into payload, but payload wins.
		Useful for fields like username, avatar_url, allowed_mentions, embeds defaults, etc.
		"""
		fmt = {}
		if isinstance(format_json, dict):
			fmt = format_json
		elif isinstance(format_json, str) and format_json.strip():
			try:
				obj = json.loads(format_json)
				if isinstance(obj, dict):
					fmt = obj
			except Exception:
				fmt = {}

		out = dict(fmt)
		out.setdefault("username", self._default_username)
		out.setdefault("avatar_url", self._default_avatar_url)
		out.update(payload)
		return out

	def _send_and_record(self, webhook_row: dict[str, Any], payload: dict[str, Any]) -> DiscordSendResult:
		webhook_id = str(webhook_row.get("id"))
		url = webhook_row.get("webhook_url")
		if not url:
			return DiscordSendResult(ok=False, status_code=None, error="Webhook URL missing.")

		try:
			logger.info("Sending webhook id=%s", webhook_id)
			resp = requests.post(
				url,
				json=self._apply_format_json(payload, None),
				timeout=self._timeout_s,
				headers={"Content-Type": "application/json"},
			)
			ok = 200 <= resp.status_code < 300
			logger.info("Webhook id=%s response status=%s body=%s", webhook_id, resp.status_code, self._truncate(resp.text, 500))

			self._record_webhook_result(
				webhook_id=webhook_id,
				ok=ok,
				status_code=resp.status_code,
				error=None if ok else self._truncate(resp.text, 2000),
			)

			return DiscordSendResult(ok=ok, status_code=resp.status_code, error=None if ok else "Non-2xx response.")
		except requests.RequestException as e:
			err = f"{type(e).__name__}: {e}"
			self._record_webhook_result(
				webhook_id=webhook_id,
				ok=False,
				status_code=None,
				error=self._truncate(err, 2000),
			)
			return DiscordSendResult(ok=False, status_code=None, error=err)

	def send_test_message(self, webhook_url: str, payload: dict[str, Any]) -> DiscordSendResult:
		if not webhook_url:
			return DiscordSendResult(ok=False, status_code=None, error="Webhook URL missing.")
		try:
			logger.info("Sending webhook test to url=%s", webhook_url)
			resp = requests.post(
				webhook_url,
				json=self._apply_format_json(payload, None),
				timeout=self._verify_timeout_s,
				headers={"Content-Type": "application/json"},
			)
			ok = 200 <= resp.status_code < 300
			logger.info("Webhook test response status=%s body=%s", resp.status_code, self._truncate(resp.text, 500))
			return DiscordSendResult(ok=ok, status_code=resp.status_code, error=None if ok else "Non-2xx response.")
		except requests.RequestException as e:
			err = f"{type(e).__name__}: {e}"
			logger.warning("Webhook test failed: %s", err)
			return DiscordSendResult(ok=False, status_code=None, error=err)

	def _record_webhook_result(self, *, webhook_id: str, ok: bool, status_code: int | None, error: str | None) -> None:
		now = datetime.now(timezone.utc)

		updates: dict[str, Any] = {
			"last_sent_at": now,
			"last_status_code": status_code,
			"last_error": error,
			"updated_at": now,
		}

		if ok:
			updates["consecutive_failures"] = 0
		else:
			rows, _ = self._client.get_rows_with_filters(
				"discord_webhooks",
				equalities={"id": webhook_id},
				page_limit=1,
				page_num=0,
			)
			cur = int(rows[0].get("consecutive_failures", 0)) if rows else 0
			updates["consecutive_failures"] = cur + 1

		self._client.update_rows_with_equalities(
			"discord_webhooks",
			updates,
			{"id": webhook_id},
		)

	@staticmethod
	def _truncate(s: str, n: int) -> str:
		s = "" if s is None else str(s)
		return s if len(s) <= n else (s[: max(0, n - 3)] + "...")
