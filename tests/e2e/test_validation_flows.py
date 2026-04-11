from __future__ import annotations

import uuid

import pytest

from tests.e2e.support import MEMBER_EMAIL

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e


def _unique_email(prefix: str) -> str:
	return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def test_login_rejects_invalid_password(anon_page, base_url: str) -> None:
	anon_page.goto(f"{base_url}/login", wait_until="domcontentloaded")
	anon_page.locator('input[name="email"]').fill(MEMBER_EMAIL)
	anon_page.locator('input[name="password"]').fill("WrongPassword123!")
	anon_page.get_by_role("button", name="Log in").click()
	expect(anon_page.locator("#login-form [data-form-message]")).to_contain_text("Invalid email or password.")
	expect(anon_page.locator('[data-page="login"]')).to_be_visible()


def test_register_rejects_invalid_fields(anon_page, base_url: str, e2e_seed) -> None:
	email = _unique_email("invalid-register")

	anon_page.goto(f"{base_url}/register", wait_until="domcontentloaded")
	anon_page.locator('select[name="referral_source"]').select_option("friend")
	anon_page.locator('input[name="first_name"]').fill("Invalid")
	anon_page.locator('input[name="last_name"]').fill("User")
	anon_page.locator('input[name="email"]').fill(email)
	anon_page.locator('input[name="password"]').fill("RegisterPass123!")
	anon_page.locator('input[name="repeat_password"]').fill("MismatchPass123!")
	anon_page.locator('button[data-submit-route="/register"]').click()
	expect(anon_page.locator("#register-form [data-form-message]")).to_contain_text("Passwords do not match.")

	rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"pending_users",
		equalities={"email": email},
		page_limit=1,
		page_num=0,
	)
	assert rows == []


def test_discord_webhook_verification_rejects_invalid_code(member_page, base_url: str, live_server, e2e_seed) -> None:
	webhook_url = f"https://discord.example/{uuid.uuid4()}"

	member_page.goto(f"{base_url}/discord-webhook-registration", wait_until="domcontentloaded")
	member_page.locator('input[name="name"]').fill("Bad Code Webhook")
	member_page.locator('input[name="webhook_url"]').fill(webhook_url)
	member_page.locator('select[name="event_key"]').select_option("test.public")
	member_page.get_by_role("button", name="Send Verification Code").click()
	member_page.wait_for_url("**/token?vid=*")

	actual_code = live_server.state.last_webhook_verification_code()
	invalid_code = "000000" if actual_code != "000000" else "999999"
	member_page.locator('input[name="verification_code"]').fill(invalid_code)
	member_page.get_by_role("button", name="Submit Code").click()
	expect(member_page.locator("#discord-webhook-verify-form [data-form-message]")).to_contain_text("Invalid verification code.")

	rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"discord_webhook_registrations",
		equalities={"webhook_url": webhook_url, "event_key": "test.public"},
		page_limit=5,
		page_num=0,
	)
	assert rows == []


def test_discord_webhook_registration_reactivates_existing_subscription(member_page, base_url: str, e2e_seed) -> None:
	webhook_url = f"https://discord.example/{uuid.uuid4()}"
	record = e2e_seed.create_inactive_webhook_subscription(
		webhook_url=webhook_url,
		event_key="test.user",
	)

	member_page.goto(f"{base_url}/discord-webhook-registration", wait_until="domcontentloaded")
	member_page.locator('input[name="name"]').fill("Existing Webhook")
	member_page.locator('input[name="webhook_url"]').fill(webhook_url)
	member_page.locator('select[name="event_key"]').select_option("test.user")
	member_page.get_by_role("button", name="Send Verification Code").click()
	member_page.wait_for_url(f"{base_url}/discord-webhook/verified?status=reactivated")
	expect(member_page.locator('[data-page="discord-webhook-verified"]')).to_be_visible()

	rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"discord_webhook_subscriptions",
		raw_conditions=["id = %s"],
		raw_params=[record["subscription"]["id"]],
		page_limit=1,
		page_num=0,
	)
	assert rows
	assert rows[0]["is_active"] is True


def test_minecraft_request_rejects_duplicate_pending_username(anon_page, base_url: str, e2e_seed) -> None:
	username = f"Pending{uuid.uuid4().hex[:8]}"
	e2e_seed.create_pending_minecraft_request(mc_username=username)

	anon_page.goto(f"{base_url}/minecraft", wait_until="domcontentloaded")
	anon_page.locator('input[name="first_name"]').fill("Mine")
	anon_page.locator('input[name="last_name"]').fill("Tester")
	anon_page.locator('input[name="email"]').fill(_unique_email("minecraft-pending"))
	anon_page.locator('input[name="mc_username"]').fill(username)
	anon_page.locator('select[name="who_are_you"]').select_option("friend")
	anon_page.locator('textarea[name="additional_info"]').fill("Duplicate pending request")
	anon_page.get_by_role("button", name="Submit Request").click()
	expect(anon_page.locator("#minecraft-registration-form [data-form-message]")).to_contain_text(
		"That Minecraft username already has an application on file."
	)

	rows = e2e_seed.interface.client.execute_query(
		"SELECT COUNT(*) AS count FROM minecraft_registrations WHERE LOWER(mc_username) = LOWER(%s) AND status = 'pending'",
		[username],
	)
	assert rows
	assert int(rows[0]["count"]) == 1


def test_minecraft_request_rejects_already_whitelisted_username(anon_page, base_url: str, e2e_seed) -> None:
	username = f"White{uuid.uuid4().hex[:8]}"
	e2e_seed.create_minecraft_whitelist_entry(mc_username=username)

	anon_page.goto(f"{base_url}/minecraft", wait_until="domcontentloaded")
	anon_page.locator('input[name="first_name"]').fill("Mine")
	anon_page.locator('input[name="last_name"]').fill("Tester")
	anon_page.locator('input[name="email"]').fill(_unique_email("minecraft-white"))
	anon_page.locator('input[name="mc_username"]').fill(username)
	anon_page.locator('select[name="who_are_you"]').select_option("friend")
	anon_page.locator('textarea[name="additional_info"]').fill("Already whitelisted request")
	anon_page.get_by_role("button", name="Submit Request").click()
	expect(anon_page.locator("#minecraft-registration-form [data-form-message]")).to_contain_text(
		"That Minecraft username is already whitelisted."
	)

	rows = e2e_seed.interface.client.execute_query(
		"SELECT COUNT(*) AS count FROM minecraft_registrations WHERE LOWER(mc_username) = LOWER(%s)",
		[username],
	)
	assert rows
	assert int(rows[0]["count"]) == 0
