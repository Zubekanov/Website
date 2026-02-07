from __future__ import annotations

from util.navbars import visibility


def test_nav_entry_visible_default_true():
	assert visibility.nav_entry_visible({"label": "Home"}, user=None, is_admin=False) is True


def test_nav_entry_visible_role_tokens():
	entry = {"label": "Admin", "visibility": ["admin"]}
	assert visibility.nav_entry_visible(entry, user=None, is_admin=False) is False
	assert visibility.nav_entry_visible(entry, user={"id": "u1"}, is_admin=False) is False
	assert visibility.nav_entry_visible(entry, user={"id": "a1"}, is_admin=True) is True


def test_filter_nav_items_filters_sections_and_entries():
	items = [
		{
			"type": "mega",
			"label": "Services",
			"sections": [
				{
					"label": "General",
					"items": [
						{"label": "Public", "href": "/public"},
						{"label": "Admin only", "href": "/admin", "visibility": ["admin"]},
					],
				}
			],
		},
		{"type": "link", "label": "Admin", "href": "/admin", "visibility": ["admin"]},
	]

	member_view = visibility.filter_nav_items(items, user={"id": "u1"}, is_admin=False)
	assert len(member_view) == 1
	assert member_view[0]["sections"][0]["items"] == [{"label": "Public", "href": "/public"}]

	admin_view = visibility.filter_nav_items(items, user={"id": "a1"}, is_admin=True)
	assert len(admin_view) == 2
	assert len(admin_view[0]["sections"][0]["items"]) == 2
