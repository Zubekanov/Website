from __future__ import annotations

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
	("path", "anon_target", "anon_selector"),
	[
		("/profile", "/login", '[data-page="login"]'),
		("/delete-account", "/", '[data-page="landing"]'),
	],
)
def test_member_route_access_matrix(
	anon_page,
	member_page,
	admin_page,
	base_url: str,
	path: str,
	anon_target: str,
	anon_selector: str,
) -> None:
	anon_page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
	expect(anon_page).to_have_url(f"{base_url}{anon_target}")
	expect(anon_page.locator(anon_selector)).to_be_visible()

	member_response = member_page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
	assert member_response is not None
	assert member_response.status == 200

	admin_response = admin_page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
	assert admin_response is not None
	assert admin_response.status == 200


@pytest.mark.parametrize(
	"path",
	[
		"/api-access-application",
		"/psql-interface",
		"/admin",
		"/admin/users",
		"/admin/audiobookshelf-approvals",
		"/admin/discord-webhook-approvals",
		"/admin/minecraft-approvals",
		"/admin/api-access-approvals",
		"/admin/email-debug",
		"/admin/frontend-test",
	],
)
def test_admin_route_access_matrix(anon_page, member_page, admin_page, base_url: str, path: str) -> None:
	anon_page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
	expect(anon_page).to_have_url(f"{base_url}/login")
	expect(anon_page.locator('[data-page="login"]')).to_be_visible()

	member_response = member_page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
	assert member_response is not None
	assert member_response.status == 403
	expect(member_page.get_by_text("Admin access required.", exact=False)).to_be_visible()

	admin_response = admin_page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
	assert admin_response is not None
	assert admin_response.status == 200
