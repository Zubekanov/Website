from __future__ import annotations

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
	("path", "expected_selector", "expected_status"),
	[
		("/", '[data-page="landing"]', 200),
		("/readme", "text=README.md", 200),
		("/server-metrics", '[data-page="metrics"]', 200),
		("/login", '[data-page="login"]', 200),
		("/register", '[data-page="register"]', 200),
		("/verify-email", '[data-page="verify-email"]', 200),
		("/reset-password", '[data-page="reset-password"]', 200),
		("/audiobookshelf-registration", '[data-page="audiobookshelf-registration"]', 200),
		("/discord-webhook-registration", '[data-page="discord-webhook-registration"]', 200),
		("/discord-webhook/verify", '[data-page="discord-webhook-verify"]', 200),
		("/discord-webhook/verified", '[data-page="discord-webhook-verified"]', 200),
		("/minecraft", '[data-page="minecraft"]', 200),
		("/popugame", 'button:has-text("Host Multiplayer Game")', 200),
		("/popugame/invalid", "text=Invalid PopuGame Link", 404),
		("/integration/removed", '[data-page="integration-removed"]', 200),
	],
)
def test_public_routes_smoke(anon_page, base_url: str, path: str, expected_selector: str, expected_status: int) -> None:
	response = anon_page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
	assert response is not None
	assert response.status == expected_status
	expect(anon_page.locator(expected_selector).first).to_be_visible()
