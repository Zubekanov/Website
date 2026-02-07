from __future__ import annotations

from datetime import datetime, timedelta, timezone

from util.verification_utils import build_verification_expiry_text


def test_build_verification_expiry_text_none():
	assert build_verification_expiry_text(None) == "This link may be invalid due to a server error."


def test_build_verification_expiry_text_expired():
	now = datetime(2026, 1, 1, tzinfo=timezone.utc)
	expires_at = now - timedelta(seconds=1)
	assert build_verification_expiry_text(expires_at, now=now) == "This link has expired."


def test_build_verification_expiry_text_minutes():
	now = datetime(2026, 1, 1, tzinfo=timezone.utc)
	expires_at = now + timedelta(minutes=5, seconds=5)
	assert build_verification_expiry_text(expires_at, now=now) == "This link will expire in 6 minutes."


def test_build_verification_expiry_text_hours():
	now = datetime(2026, 1, 1, tzinfo=timezone.utc)
	expires_at = now + timedelta(hours=2, minutes=1)
	assert build_verification_expiry_text(expires_at, now=now) == "This link will expire in 3 hours."
