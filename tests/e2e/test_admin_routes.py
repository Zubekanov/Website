from __future__ import annotations

import uuid

import pytest

from tests.e2e.support import MEMBER_USER_ID

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e


def test_admin_dashboard_counts(admin_page, base_url: str, e2e_seed) -> None:
	e2e_seed.create_pending_audiobookshelf_request()
	e2e_seed.create_pending_discord_webhook_request()
	e2e_seed.create_pending_minecraft_request()
	e2e_seed.create_pending_api_access_request()

	admin_page.goto(f"{base_url}/admin", wait_until="domcontentloaded")
	expect(admin_page.locator('[data-page="admin-dashboard"]')).to_be_visible()
	expect(admin_page.locator("a.admin-card", has_text="Minecraft Requests")).to_contain_text("1")
	expect(admin_page.locator("a.admin-card", has_text="Discord Webhook Requests")).to_contain_text("1")
	expect(admin_page.locator("a.admin-card", has_text="API Access Requests")).to_contain_text("1")
	expect(admin_page.locator("a.admin-card", has_text="Audiobookshelf Requests")).to_contain_text("1")


@pytest.mark.parametrize(
	("path", "seed_fn"),
	[
		("/admin/audiobookshelf-approvals", "create_pending_audiobookshelf_request"),
		("/admin/discord-webhook-approvals", "create_pending_discord_webhook_request"),
		("/admin/minecraft-approvals", "create_pending_minecraft_request"),
		("/admin/api-access-approvals", "create_pending_api_access_request"),
	],
)
def test_admin_approval_pages_support_approve_action(admin_page, base_url: str, e2e_seed, path: str, seed_fn: str) -> None:
	getattr(e2e_seed, seed_fn)()
	admin_page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
	expect(admin_page.locator("[data-approval-card]")).to_have_count(1)
	admin_page.locator('[data-approval-action="approve"]').click()
	expect(admin_page.locator("[data-approval-card]")).to_have_count(0)


def test_admin_user_management_promote_and_demote(admin_page, base_url: str) -> None:
	admin_page.goto(f"{base_url}/admin/users", wait_until="domcontentloaded")
	member_card = admin_page.locator(f'[data-user-card][data-user-id="{MEMBER_USER_ID}"]')
	badge = member_card.locator(".admin-user-badge")
	expect(badge).to_have_text("MEMBER")

	member_card.locator('[data-user-action="promote"]').click()
	expect(badge).to_have_text("ADMIN")

	admin_page.goto(f"{base_url}/admin/users", wait_until="domcontentloaded")
	member_card = admin_page.locator(f'[data-user-card][data-user-id="{MEMBER_USER_ID}"]')
	member_card.locator('[data-user-action="demote"]').click()
	expect(member_card.locator(".admin-user-badge")).to_have_text("MEMBER")


def test_admin_email_debug_submit(admin_page, base_url: str, live_server) -> None:
	admin_page.goto(f"{base_url}/admin/email-debug", wait_until="domcontentloaded")
	admin_page.locator('input[name="to_email"]').fill("debug@example.com")
	admin_page.locator('input[name="subject"]').fill("Playwright debug email")
	admin_page.locator('textarea[name="body"]').fill("This is a debug email from Playwright.")
	admin_page.get_by_role("button", name="Send Debug Email").click()
	expect(admin_page.locator("#debug-email-form [data-form-message]")).to_contain_text("Email sent.")
	assert live_server.state.last_email()["subject"] == "Playwright debug email"


def test_admin_api_access_application_submit(admin_page, base_url: str, e2e_seed) -> None:
	service_name = f"Playwright Service {uuid.uuid4().hex[:8]}"

	admin_page.goto(f"{base_url}/api-access-application", wait_until="domcontentloaded")
	admin_page.locator('input[name="service_name"]').fill(service_name)
	admin_page.locator('[data-scope-dropdown]').select_option("metrics.read")
	admin_page.locator('textarea[name="use_case"]').fill("Validate the admin API application browser flow.")
	admin_page.get_by_role("button", name="Submit Application").click()
	admin_page.wait_for_url(f"{base_url}/profile")
	expect(admin_page.locator('[data-page="profile"] [data-linked-integrations]')).to_be_visible()

	rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"api_access_registrations",
		equalities={"service_name": service_name, "status": "approved"},
		page_limit=1,
		page_num=0,
	)
	assert rows


def test_admin_psql_interface_smoke(admin_page, base_url: str) -> None:
	response = admin_page.goto(f"{base_url}/psql-interface", wait_until="domcontentloaded")
	assert response is not None
	assert response.status == 200
	expect(admin_page.get_by_role("heading", name="Database Admin")).to_be_visible()


def test_admin_frontend_test_smoke(admin_page, base_url: str) -> None:
	response = admin_page.goto(f"{base_url}/admin/frontend-test", wait_until="domcontentloaded")
	assert response is not None
	assert response.status == 200
	expect(admin_page.get_by_role("heading", name="Frontend Test Page")).to_be_visible()
