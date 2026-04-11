from __future__ import annotations

import uuid

import pytest

from tests.e2e.support import MEMBER_EMAIL

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e


def _unique_email(prefix: str) -> str:
	return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def test_profile_password_change_flow(member_page, base_url: str) -> None:
	member_page.goto(f"{base_url}/profile", wait_until="domcontentloaded")
	member_page.locator("[data-password-panel-toggle]").click()
	member_page.locator("[data-password-input]").fill("ChangedPass123!")
	member_page.locator("[data-password-confirm]").fill("ChangedPass123!")
	member_page.locator("[data-password-submit]").click()
	expect(member_page.locator("[data-password-message]")).to_contain_text("Password updated.")

	member_page.goto(f"{base_url}/logout", wait_until="domcontentloaded")
	member_page.goto(f"{base_url}/login", wait_until="domcontentloaded")
	member_page.locator('input[name="email"]').fill(MEMBER_EMAIL)
	member_page.locator('input[name="password"]').fill("ChangedPass123!")
	member_page.get_by_role("button", name="Log in").click()
	member_page.wait_for_url(f"{base_url}/profile")
	expect(member_page.locator('[data-page="profile"] [data-linked-integrations]')).to_be_visible()


def test_audiobookshelf_registration_flow(anon_page, base_url: str, e2e_seed) -> None:
	email = _unique_email("audiobookshelf")

	anon_page.goto(f"{base_url}/audiobookshelf-registration", wait_until="domcontentloaded")
	anon_page.locator('input[name="first_name"]').fill("Audio")
	anon_page.locator('input[name="last_name"]').fill("Tester")
	anon_page.locator('input[name="email"]').fill(email)
	anon_page.locator('textarea[name="additional_info"]').fill("Playwright request")
	anon_page.get_by_role("button", name="Submit Registration").click()
	anon_page.wait_for_url(f"{base_url}/")
	expect(anon_page.locator('[data-page="landing"]')).to_be_visible()

	rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"audiobookshelf_registrations",
		equalities={"email": email, "status": "pending"},
		page_limit=1,
		page_num=0,
	)
	assert rows


def test_minecraft_request_flow(anon_page, base_url: str, e2e_seed) -> None:
	email = _unique_email("minecraft")
	username = f"PW{uuid.uuid4().hex[:10]}"

	anon_page.goto(f"{base_url}/minecraft", wait_until="domcontentloaded")
	expect(anon_page.locator('[data-page="minecraft"]')).to_be_visible()
	anon_page.locator('input[name="first_name"]').fill("Mine")
	anon_page.locator('input[name="last_name"]').fill("Tester")
	anon_page.locator('input[name="email"]').fill(email)
	anon_page.locator('input[name="mc_username"]').fill(username)
	anon_page.locator('select[name="who_are_you"]').select_option("friend")
	anon_page.locator('textarea[name="additional_info"]').fill("Playwright whitelist request")
	anon_page.get_by_role("button", name="Submit Request").click()
	expect(anon_page.locator("#minecraft-registration-form [data-form-message]")).to_contain_text("Request submitted.")

	rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"minecraft_registrations",
		equalities={"mc_username": username, "status": "pending"},
		page_limit=1,
		page_num=0,
	)
	assert rows


def test_discord_webhook_registration_flow(member_page, base_url: str, live_server, e2e_seed) -> None:
	webhook_url = f"https://discord.example/{uuid.uuid4()}"

	member_page.goto(f"{base_url}/discord-webhook-registration", wait_until="domcontentloaded")
	member_page.locator('input[name="name"]').fill("Playwright Webhook")
	member_page.locator('input[name="webhook_url"]').fill(webhook_url)
	member_page.locator('select[name="event_key"]').select_option("test.public")
	member_page.get_by_role("button", name="Send Verification Code").click()
	member_page.wait_for_url("**/token?vid=*")

	code = live_server.state.last_webhook_verification_code()
	member_page.locator('input[name="verification_code"]').fill(code)
	member_page.get_by_role("button", name="Submit Code").click()
	member_page.wait_for_url(f"{base_url}/discord-webhook/verified?status=submitted")
	expect(member_page.locator('[data-page="discord-webhook-verified"]')).to_be_visible()

	rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"discord_webhook_registrations",
		equalities={"webhook_url": webhook_url, "event_key": "test.public", "status": "pending"},
		page_limit=1,
		page_num=0,
	)
	assert rows
