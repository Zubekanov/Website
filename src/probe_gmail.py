#!/usr/bin/env python3
"""Proactively exchange the Gmail refresh token for a new access token.

Run this on a schedule (e.g. biweekly) to:
  - Confirm the refresh token is still valid before it's needed for real mail.
  - Keep the OAuth session active so Google doesn't revoke it for inactivity.

Emits a Discord notification via the system.alerts event key on success or failure.
"""
import sys
import os
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from util.integrations.email.email_interface import GmailEmailSender
from util.integrations.discord.webhook_interface import DiscordWebhookEmitter
from sql.psql_interface import PSQLInterface

EVENT_KEY = "system.alerts"


def _next_run_ts() -> int:
	"""UNIX timestamp of the next 1st or 15th of the month at 03:00 UTC."""
	now = datetime.now(timezone.utc)
	candidates = []
	for day in (1, 15):
		try:
			candidate = now.replace(day=day, hour=3, minute=0, second=0, microsecond=0)
		except ValueError:
			continue
		if candidate <= now:
			month = now.month + 1
			year = now.year + (1 if month > 12 else 0)
			month = ((month - 1) % 12) + 1
			try:
				candidate = candidate.replace(year=year, month=month)
			except ValueError:
				continue
		candidates.append(candidate)
	if not candidates:
		return int((now.replace(hour=3, minute=0, second=0, microsecond=0)).timestamp()) + 14 * 86400
	return int(min(candidates).timestamp())


def _notify(emitter: DiscordWebhookEmitter, *, ok: bool, error: str | None = None) -> None:
	now_ts = int(datetime.now(timezone.utc).timestamp())
	if ok:
		next_ts = _next_run_ts()
		embed = {
			"title": "Gmail Token Refreshed",
			"description": "Access token exchanged successfully.",
			"color": 0x2ECC71,
			"fields": [
				{"name": "Refreshed at", "value": f"<t:{now_ts}:F>", "inline": True},
				{"name": "Next refresh", "value": f"<t:{next_ts}:F> (<t:{next_ts}:R>)", "inline": True},
			],
			"timestamp": datetime.now(timezone.utc).isoformat(),
		}
	else:
		embed = {
			"title": "\u26a0\ufe0f Gmail Token Refresh Failed",
			"description": (
				"The Gmail OAuth access token could not be refreshed.\n"
				"**Manual intervention required** \u2014 re-run `bootstrap_gmail.py` to complete a new OAuth flow."
			),
			"color": 0xE74C3C,
			"fields": [
				{"name": "Failed at", "value": f"<t:{now_ts}:F>", "inline": True},
				{"name": "Error", "value": f"```{(error or 'Unknown error')[:900]}```", "inline": False},
			],
			"timestamp": datetime.now(timezone.utc).isoformat(),
		}

	try:
		results = emitter.emit_event(EVENT_KEY, payload={"embeds": [embed]})
		log.info("Discord notification dispatched to %d webhook(s).", len(results))
	except Exception as exc:
		log.warning("Failed to send Discord notification: %s", exc)


def main() -> int:
	# Best-effort Discord emitter — if DB is unavailable, we still attempt the token refresh.
	emitter = None
	try:
		psql = PSQLInterface()
		emitter = DiscordWebhookEmitter(psql)
	except Exception as exc:
		log.warning("Could not initialise Discord emitter (notifications disabled): %s", exc)

	# Initialise email sender (reads gmail.conf).
	try:
		sender = GmailEmailSender()
	except Exception as exc:
		log.error("Failed to initialise GmailEmailSender: %s", exc)
		if emitter:
			_notify(emitter, ok=False, error=str(exc))
		return 1

	# Exchange refresh token for a new access token.
	try:
		token = sender._refresh_access_token()
	except Exception as exc:
		log.error("Gmail token refresh failed: %s", exc)
		if emitter:
			_notify(emitter, ok=False, error=str(exc))
		return 1

	if not token:
		msg = "Token refresh returned an empty token."
		log.error(msg)
		if emitter:
			_notify(emitter, ok=False, error=msg)
		return 1

	log.info("Gmail access token refreshed successfully (len=%d).", len(token))
	if emitter:
		_notify(emitter, ok=True)
	return 0


if __name__ == "__main__":
	sys.exit(main())
