from __future__ import annotations

from datetime import datetime, timezone


def build_verification_expiry_text(expires_at: datetime | None, *, now: datetime | None = None) -> str:
	if not expires_at:
		return "This link may be invalid due to a server error."

	current = now or datetime.now(timezone.utc)
	if expires_at.tzinfo is None:
		expires_at = expires_at.replace(tzinfo=timezone.utc)

	remaining = max(0, int((expires_at - current).total_seconds()))
	if remaining <= 0:
		return "This link has expired."
	if remaining < 3600:
		minutes = (remaining + 59) // 60
		unit = "minute" if minutes == 1 else "minutes"
		return f"This link will expire in {minutes} {unit}."
	hours = (remaining + 3599) // 3600
	unit = "hour" if hours == 1 else "hours"
	return f"This link will expire in {hours} {unit}."
