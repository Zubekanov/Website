from __future__ import annotations

from datetime import datetime, timezone

from app.metrics_utils import normalize_metrics_query


def test_normalize_metrics_query_rejects_count_and_since():
	query, error = normalize_metrics_query(
		count=100,
		since="2026-01-01T00:00:00+00:00",
		window=None,
		bucket=None,
		format_ts=False,
	)
	assert query is None
	assert error == "Do not specify both 'count' and 'since' parameters."


def test_normalize_metrics_query_rejects_invalid_since():
	query, error = normalize_metrics_query(
		count=None,
		since="not-a-date",
		window=None,
		bucket=None,
		format_ts=False,
	)
	assert query is None
	assert error == "Invalid 'since' timestamp format. Use ISO 8601 format."


def test_normalize_metrics_query_uses_default_count_for_old_since():
	now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
	query, error = normalize_metrics_query(
		count=None,
		since="2026-01-01T09:00:00+00:00",
		window=None,
		bucket=None,
		format_ts=False,
		now=now,
	)
	assert error is None
	assert query.count == 720


def test_normalize_metrics_query_rounds_recent_since_and_computes_count():
	now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
	query, error = normalize_metrics_query(
		count=None,
		since="2026-01-01T11:59:51+00:00",
		window=None,
		bucket=None,
		format_ts=True,
		now=now,
	)
	assert error is None
	assert query.since_dt.isoformat() == "2026-01-01T11:59:50+00:00"
	assert query.count == 3
