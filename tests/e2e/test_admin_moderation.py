from __future__ import annotations

import pytest

from tests.e2e.support import MEMBER_EMAIL, MEMBER_USER_ID

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e


def _fetch_one(interface, table: str, row_id: object) -> dict:
	rows, _ = interface.client.get_rows_with_filters(
		table,
		raw_conditions=["id = %s"],
		raw_params=[row_id],
		page_limit=1,
		page_num=0,
	)
	assert rows
	return rows[0]


@pytest.mark.parametrize(
	("path", "seed_fn", "table", "expected_is_active"),
	[
		("/admin/audiobookshelf-approvals", "create_pending_audiobookshelf_request", "audiobookshelf_registrations", False),
		("/admin/discord-webhook-approvals", "create_pending_discord_webhook_request", "discord_webhook_registrations", None),
		("/admin/minecraft-approvals", "create_pending_minecraft_request", "minecraft_registrations", None),
		("/admin/api-access-approvals", "create_pending_api_access_request", "api_access_registrations", False),
	],
)
def test_admin_approval_pages_support_deny_action(
	admin_page,
	base_url: str,
	e2e_seed,
	path: str,
	seed_fn: str,
	table: str,
	expected_is_active: bool | None,
) -> None:
	row = getattr(e2e_seed, seed_fn)()
	admin_page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
	expect(admin_page.locator("[data-approval-card]")).to_have_count(1)
	admin_page.locator('[data-approval-action="deny"]').click()
	expect(admin_page.locator("[data-approval-card]")).to_have_count(0)

	updated = _fetch_one(e2e_seed.interface, table, row["id"])
	assert updated["status"] == "denied"
	if expected_is_active is not None:
		assert updated["is_active"] is expected_is_active


@pytest.mark.parametrize(
	("integration_type", "expected_active_status"),
	[
		("discord_webhook", "active"),
		("minecraft", "whitelisted"),
		("audiobookshelf", "approved"),
	],
)
def test_admin_can_disable_and_enable_integrations(
	admin_page,
	base_url: str,
	e2e_seed,
	integration_type: str,
	expected_active_status: str,
) -> None:
	if integration_type == "discord_webhook":
		record = e2e_seed.create_active_webhook_subscription(event_key="test.user")
		row_id = record["webhook"]["id"]
		table = "discord_webhooks"
	elif integration_type == "minecraft":
		record = e2e_seed.create_minecraft_whitelist_entry(mc_username="AdminToggle")
		row_id = record["id"]
		table = "minecraft_whitelist"
	else:
		record = e2e_seed.create_active_audiobookshelf_registration()
		row_id = record["id"]
		table = "audiobookshelf_registrations"

	admin_page.goto(f"{base_url}/admin/users", wait_until="domcontentloaded")
	member_card = admin_page.locator(f'[data-user-card][data-user-id="{MEMBER_USER_ID}"]')
	card = member_card.locator(f'[data-integration-card="{integration_type}"]')
	card.locator("[data-integration-delete]").click()

	modal = admin_page.locator("[data-integration-modal]")
	modal.locator("[data-integration-reason]").select_option("policy")
	modal.locator("[data-integration-confirm]").check()
	modal.locator("[data-integration-submit]").click()
	expect(card).to_have_attribute("data-integration-status", "suspended")

	row = _fetch_one(e2e_seed.interface, table, row_id)
	assert row["is_active"] is False

	card.locator("[data-integration-enable]").click()
	expect(card).to_have_attribute("data-integration-status", expected_active_status)

	row = _fetch_one(e2e_seed.interface, table, row_id)
	assert row["is_active"] is True


def test_admin_user_delete_flow(admin_page, base_url: str, e2e_seed, live_server) -> None:
	e2e_seed.create_active_webhook_subscription(event_key="test.user")
	e2e_seed.create_minecraft_whitelist_entry(mc_username="AdminDelete")
	e2e_seed.create_active_audiobookshelf_registration()

	admin_page.goto(f"{base_url}/admin/users", wait_until="domcontentloaded")
	member_card = admin_page.locator(f'[data-user-card][data-user-id="{MEMBER_USER_ID}"]')
	member_card.locator('[data-user-action="delete"]').click()

	modal = admin_page.locator("[data-admin-user-delete-modal]")
	modal.locator("[data-admin-user-delete-reason]").select_option("policy")
	modal.locator("[data-admin-user-delete-confirm]").check()
	modal.locator("[data-admin-user-delete-submit]").click()
	expect(admin_page.locator(f'[data-user-card][data-user-id="{MEMBER_USER_ID}"]')).to_have_count(0)

	user_rows, _ = e2e_seed.interface.client.get_rows_with_filters(
		"users",
		equalities={"id": MEMBER_USER_ID},
		page_limit=1,
		page_num=0,
	)
	assert user_rows
	assert user_rows[0]["is_active"] is False

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

	assert live_server.state.last_email()["subject"] == "Account deleted by admin"
	assert MEMBER_EMAIL in live_server.state.last_email()["to_addrs"]
