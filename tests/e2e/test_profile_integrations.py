from __future__ import annotations

import pytest

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


def _build_token_seed(e2e_seed, integration_type: str) -> tuple[str, object]:
	anon_user = e2e_seed.create_anonymous_user()
	user_id = str(anon_user["id"])
	email = anon_user["email"]
	if integration_type == "discord_webhook":
		record = e2e_seed.create_active_webhook_subscription(
			user_id=user_id,
			event_key="test.public",
		)
		integration_id = record["webhook"]["id"]
	elif integration_type == "minecraft":
		record = e2e_seed.create_minecraft_whitelist_entry(
			user_id=user_id,
			email=email,
			first_name=anon_user["first_name"],
			last_name=anon_user["last_name"],
			mc_username="AnonRemoval",
		)
		integration_id = record["id"]
	else:
		record = e2e_seed.create_active_audiobookshelf_registration(
			user_id=user_id,
			email=email,
			first_name=anon_user["first_name"],
			last_name=anon_user["last_name"],
		)
		integration_id = record["id"]
	token = e2e_seed.build_integration_removal_token(
		integration_type=integration_type,
		integration_id=str(integration_id),
		user_id=user_id,
	)
	return token, integration_id


def test_profile_discord_subscription_toggle(member_page, base_url: str, e2e_seed) -> None:
	record = e2e_seed.create_active_webhook_subscription(event_key="test.user")
	subscription_id = record["subscription"]["id"]

	member_page.goto(f"{base_url}/profile", wait_until="domcontentloaded")
	card = member_page.locator('[data-subscription-card][data-subscription-event-key="test.user"]')
	expect(card).to_have_attribute("data-subscription-state", "active")

	card.locator('[data-subscription-action="unsubscribe"]').click()
	expect(card).to_have_attribute("data-subscription-state", "inactive")

	subscription = _fetch_one(e2e_seed.interface, "discord_webhook_subscriptions", subscription_id)
	assert subscription["is_active"] is False

	card.locator('[data-subscription-action="resubscribe"]').click()
	expect(card).to_have_attribute("data-subscription-state", "active")

	subscription = _fetch_one(e2e_seed.interface, "discord_webhook_subscriptions", subscription_id)
	assert subscription["is_active"] is True


@pytest.mark.parametrize(
	("integration_type", "expected_status"),
	[
		("discord_webhook", "suspended"),
		("minecraft", "suspended"),
		("audiobookshelf", "suspended"),
	],
)
def test_profile_integration_delete_modal_enforces_and_suspends(
	member_page,
	base_url: str,
	e2e_seed,
	integration_type: str,
	expected_status: str,
) -> None:
	if integration_type == "discord_webhook":
		record = e2e_seed.create_active_webhook_subscription(event_key="test.user")
		row_id = record["webhook"]["id"]
		table = "discord_webhooks"
	elif integration_type == "minecraft":
		record = e2e_seed.create_minecraft_whitelist_entry(mc_username="ProfileRemoval")
		row_id = record["id"]
		table = "minecraft_whitelist"
	else:
		record = e2e_seed.create_active_audiobookshelf_registration()
		row_id = record["id"]
		table = "audiobookshelf_registrations"

	member_page.goto(f"{base_url}/profile", wait_until="domcontentloaded")
	card = member_page.locator(f'[data-integration-card="{integration_type}"]')
	card.locator("[data-integration-delete]").click()

	modal = member_page.locator("[data-integration-modal]")
	modal.locator("[data-integration-submit]").click()
	expect(modal.locator("[data-integration-modal-message]")).to_contain_text("Please confirm deletion.")

	modal.locator("[data-integration-confirm]").check()
	modal.locator("[data-integration-submit]").click()
	expect(modal.locator("[data-integration-modal-message]")).to_contain_text("Please select a reason.")

	modal.locator("[data-integration-reason]").select_option("other")
	modal.locator("[data-integration-submit]").click()
	expect(card).to_have_attribute("data-integration-status", expected_status)

	row = _fetch_one(e2e_seed.interface, table, row_id)
	assert row["is_active"] is False


@pytest.mark.parametrize(
	("integration_type", "table"),
	[
		("discord_webhook", "discord_webhooks"),
		("minecraft", "minecraft_whitelist"),
		("audiobookshelf", "audiobookshelf_registrations"),
	],
)
def test_token_based_integration_removal_for_anonymous_integrations(
	anon_page,
	base_url: str,
	e2e_seed,
	integration_type: str,
	table: str,
) -> None:
	token, integration_id = _build_token_seed(e2e_seed, integration_type)

	anon_page.goto(f"{base_url}/integration/remove?token={token}", wait_until="domcontentloaded")
	expect(anon_page.locator('[data-page="integration-remove"]')).to_be_visible()
	anon_page.get_by_role("button", name="Remove integration").click()
	anon_page.wait_for_url(f"{base_url}/integration/removed")
	expect(anon_page.locator('[data-page="integration-removed"]')).to_be_visible()

	row = _fetch_one(e2e_seed.interface, table, integration_id)
	assert row["is_active"] is False
