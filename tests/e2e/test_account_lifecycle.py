from __future__ import annotations

import uuid

import pytest

from tests.e2e.support import MEMBER_PASSWORD, MEMBER_USER_ID

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e


def _unique_email(prefix: str) -> str:
	return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def test_register_verify_and_login_flow(anon_page, base_url: str, live_server, e2e_seed) -> None:
	email = _unique_email("register")

	anon_page.goto(f"{base_url}/register", wait_until="domcontentloaded")
	expect(anon_page.locator('[data-page="register"]')).to_be_visible()
	anon_page.locator('select[name="referral_source"]').select_option("friend")
	anon_page.locator('input[name="first_name"]').fill("Playwright")
	anon_page.locator('input[name="last_name"]').fill("User")
	anon_page.locator('input[name="email"]').fill(email)
	anon_page.locator('input[name="password"]').fill("RegisterPass123!")
	anon_page.locator('input[name="repeat_password"]').fill("RegisterPass123!")
	anon_page.locator('button[data-submit-route="/register"]').click()
	anon_page.wait_for_url(f"{base_url}/verify-email")
	expect(anon_page.locator('[data-page="verify-email"]')).to_be_visible()

	verification_link = live_server.state.last_verification_link()
	assert "/verify-email/" in verification_link
	assert email in live_server.state.last_email()["to_addrs"]

	anon_page.goto(verification_link, wait_until="domcontentloaded")
	expect(anon_page.locator('[data-page="verify-email-token"]')).to_be_visible()
	expect(anon_page.get_by_role("heading", name="Email Verified")).to_be_visible()

	anon_page.goto(f"{base_url}/login", wait_until="domcontentloaded")
	expect(anon_page.locator('[data-page="login"]')).to_be_visible()
	anon_page.locator('input[name="email"]').fill(email)
	anon_page.locator('input[name="password"]').fill("RegisterPass123!")
	anon_page.get_by_role("button", name="Log in").click()
	anon_page.wait_for_url(f"{base_url}/profile")
	expect(anon_page.locator('[data-page="profile"] [data-linked-integrations]')).to_be_visible()

	rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"users",
		equalities={"email": email},
		page_limit=1,
		page_num=0,
	)
	assert rows
	assert rows[0]["is_active"] is True


def test_delete_account_flow(member_page, base_url: str, e2e_seed) -> None:
	e2e_seed.create_active_webhook_subscription()
	e2e_seed.create_minecraft_whitelist_entry(mc_username="DeleteFlow")
	e2e_seed.create_active_audiobookshelf_registration()

	member_page.goto(f"{base_url}/profile", wait_until="domcontentloaded")
	expect(member_page.locator('[data-page="profile"]')).to_be_visible()
	member_page.locator("[data-delete-panel-toggle]").click()
	member_page.locator("[data-delete-password]").fill(MEMBER_PASSWORD)
	member_page.locator("[data-delete-submit]").click()
	expect(member_page.locator("[data-delete-message]")).to_contain_text("Account deleted.")

	member_page.goto(f"{base_url}/profile", wait_until="domcontentloaded")
	expect(member_page).to_have_url(f"{base_url}/login")
	expect(member_page.locator('[data-page="login"]')).to_be_visible()

	user_rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"users",
		equalities={"id": MEMBER_USER_ID},
		page_limit=1,
		page_num=0,
	)
	assert user_rows
	assert user_rows[0]["is_active"] is False

	session_rows = e2e_seed.interface.client.execute_query(
		"SELECT COUNT(*) AS count FROM user_sessions WHERE user_id = %s AND revoked_at IS NULL",
		[MEMBER_USER_ID],
	)
	assert session_rows
	assert int(session_rows[0]["count"]) == 0

	webhook_rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"discord_webhooks",
		equalities={"user_id": MEMBER_USER_ID},
		page_limit=10,
		page_num=0,
	)
	assert webhook_rows == []

	whitelist_rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"minecraft_whitelist",
		equalities={"user_id": MEMBER_USER_ID},
		page_limit=10,
		page_num=0,
	)
	assert whitelist_rows == []

	abs_rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"audiobookshelf_registrations",
		equalities={"user_id": MEMBER_USER_ID},
		page_limit=10,
		page_num=0,
	)
	assert abs_rows == []


def test_delete_account_rejects_invalid_password(member_page, base_url: str, e2e_seed) -> None:
	member_page.goto(f"{base_url}/profile", wait_until="domcontentloaded")
	member_page.locator("[data-delete-panel-toggle]").click()
	member_page.locator("[data-delete-password]").fill("WrongPassword123!")
	member_page.locator("[data-delete-submit]").click()
	expect(member_page.locator("[data-delete-message]")).to_contain_text("Incorrect password.")

	rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"users",
		equalities={"id": MEMBER_USER_ID},
		page_limit=1,
		page_num=0,
	)
	assert rows
	assert rows[0]["is_active"] is True


def test_logout_clears_access_to_protected_routes(member_page, base_url: str) -> None:
	member_page.goto(f"{base_url}/profile", wait_until="domcontentloaded")
	expect(member_page.locator('[data-page="profile"]')).to_be_visible()

	member_page.goto(f"{base_url}/logout", wait_until="domcontentloaded")
	expect(member_page.locator('[data-page="landing"]')).to_be_visible()

	member_page.goto(f"{base_url}/profile", wait_until="domcontentloaded")
	expect(member_page).to_have_url(f"{base_url}/login")
	expect(member_page.locator('[data-page="login"]')).to_be_visible()
