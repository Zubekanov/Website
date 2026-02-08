from __future__ import annotations

import logging
from typing import Any

from util.integrations.minecraft.amp_interface import AmpMinecraftClient, load_amp_minecraft_config

logger = logging.getLogger(__name__)


def _is_unconfigured_error(exc: Exception) -> bool:
	if isinstance(exc, FileNotFoundError):
		return True
	msg = str(exc).lower()
	if "amp_minecraft.conf" in msg and ("missing required fields" in msg or "not found" in msg or "no such file" in msg):
		return True
	return False


def sync_amp_minecraft_whitelist(
	interface: Any,
	*,
	trigger: str,
	actor_user_id: str | None = None,
	dry_run: bool = False,
	fail_hard: bool = False,
) -> dict:
	try:
		active_rows, _ = interface.client.get_rows_with_filters(
			"minecraft_whitelist",
			raw_conditions=["COALESCE(is_active, TRUE) = TRUE"],
			page_limit=5000,
			page_num=0,
			order_by="mc_username",
			order_dir="ASC",
		)
		inactive_rows, _ = interface.client.get_rows_with_filters(
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
		logger.info(
			"AMP whitelist sync trigger=%s actor_user_id=%s dry_run=%s requested_add=%s requested_remove=%s added=%s removed=%s errors=%s",
			trigger,
			actor_user_id,
			dry_run,
			result.get("requested_add"),
			result.get("requested_remove"),
			result.get("added"),
			result.get("removed"),
			len(result.get("errors") or []),
		)
		result["sync_status"] = "synced"
		return result
	except Exception as exc:
		if _is_unconfigured_error(exc):
			logger.info(
				"AMP whitelist sync skipped trigger=%s actor_user_id=%s dry_run=%s reason=amp_not_configured",
				trigger,
				actor_user_id,
				dry_run,
			)
			if fail_hard:
				raise
			return {
				"ok": False,
				"sync_status": "skipped",
				"skip_reason": "amp_not_configured",
				"message": str(exc),
				"errors": [{"action": "sync", "error": str(exc)}],
			}

		logger.exception(
			"AMP whitelist sync failed trigger=%s actor_user_id=%s dry_run=%s",
			trigger,
			actor_user_id,
			dry_run,
		)
		if fail_hard:
			raise
		return {
			"ok": False,
			"sync_status": "failed",
			"message": str(exc),
			"errors": [{"action": "sync", "error": str(exc)}],
		}
