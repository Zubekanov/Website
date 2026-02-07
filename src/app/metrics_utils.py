from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


@dataclass(frozen=True)
class MetricsQuery:
	count: int | None
	since_dt: datetime | None
	window: int | None
	bucket: int | None
	format_ts: bool


def _normalize_since(since: str, now: datetime) -> tuple[datetime | None, str | None]:
	if not since:
		return None, None
	try:
		since_dt = datetime.fromisoformat(since)
	except ValueError:
		return None, "Invalid 'since' timestamp format. Use ISO 8601 format."

	if since_dt.tzinfo is None:
		since_dt = since_dt.replace(tzinfo=timezone.utc)
	return since_dt, None


def normalize_metrics_query(
	*,
	count: int | None,
	since: str | None,
	window: int | None,
	bucket: int | None,
	format_ts: bool,
	default_count: int = 720,
	max_since_hours: int = 1,
	now: datetime | None = None,
) -> tuple[MetricsQuery | None, str | None]:
	current = now or datetime.now(timezone.utc)
	if count and since:
		return None, "Do not specify both 'count' and 'since' parameters."
	if window and since:
		return None, "Do not specify both 'window' and 'since' parameters."

	since_dt, error = _normalize_since(since or "", current)
	if error:
		return None, error

	if not count and not since_dt:
		count = default_count

	if since_dt:
		limit_dt = current - timedelta(hours=max_since_hours)
		if since_dt < limit_dt:
			count = default_count
		else:
			rounded = since_dt.replace(microsecond=0)
			rounded = rounded - timedelta(seconds=rounded.second % 5)
			delta = current - rounded
			count = int(delta.total_seconds() / 5) + 1
			since_dt = rounded

	return MetricsQuery(
		count=count,
		since_dt=since_dt,
		window=window,
		bucket=bucket,
		format_ts=format_ts,
	), None
