from __future__ import annotations

_NAV_VISIBILITY_TOKENS = {"all", "anonymous", "authenticated", "member", "admin"}


def nav_entry_visible(entry: dict, user: dict | None, is_admin: bool) -> bool:
	raw = entry.get("visibility")
	if raw is None:
		return True

	tokens: list[str]
	if isinstance(raw, str):
		tokens = [raw]
	elif isinstance(raw, (list, tuple, set)):
		tokens = [str(v) for v in raw]
	else:
		return False

	normalized = {t.strip().lower() for t in tokens if t and t.strip()}
	if not normalized:
		return True
	if "all" in normalized:
		return True

	allowed = normalized.intersection(_NAV_VISIBILITY_TOKENS)
	if not allowed:
		return False

	if "anonymous" in allowed and user is None:
		return True
	if "authenticated" in allowed and user is not None:
		return True
	if "member" in allowed and user is not None and not is_admin:
		return True
	if "admin" in allowed and user is not None and is_admin:
		return True
	return False


def filter_nav_items(items: list[dict], user: dict | None, is_admin: bool) -> list[dict]:
	filtered: list[dict] = []
	for item in items:
		if not isinstance(item, dict):
			continue
		if not nav_entry_visible(item, user, is_admin):
			continue

		if item.get("type") != "mega":
			filtered.append(item)
			continue

		sections: list[dict] = []
		for section in item.get("sections", []):
			if not isinstance(section, dict):
				continue
			if not nav_entry_visible(section, user, is_admin):
				continue
			section_type = section.get("type")
			if section_type == "github_repos":
				sections.append(section)
				continue

			visible_entries = [
				entry for entry in section.get("items", [])
				if isinstance(entry, dict) and nav_entry_visible(entry, user, is_admin)
			]
			if not visible_entries:
				continue
			section_copy = dict(section)
			section_copy["items"] = visible_entries
			sections.append(section_copy)

		if not sections:
			continue
		item_copy = dict(item)
		item_copy["sections"] = sections
		filtered.append(item_copy)
	return filtered
