from __future__ import annotations

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e


def test_audiobookshelf_redirect_when_service_is_healthy(anon_page, base_url: str, live_server) -> None:
	live_server.state.set_audiobookshelf_probe_status(200)

	anon_page.goto(f"{base_url}/audiobookshelf", wait_until="domcontentloaded")
	assert anon_page.url.startswith("https://audiobookshelf.zubekanov.com/")


def test_audiobookshelf_unavailable_page_when_probe_fails(anon_page, base_url: str, live_server) -> None:
	live_server.state.set_audiobookshelf_probe_status(503)

	anon_page.goto(f"{base_url}/audiobookshelf", wait_until="domcontentloaded")
	expect(anon_page.locator('[data-page="audiobookshelf-unavailable"]')).to_be_visible()
	expect(anon_page.get_by_text("HTTP 503", exact=False)).to_be_visible()


def test_metrics_page_hydrates_kpis_without_browser_errors(anon_page, base_url: str) -> None:
	console_errors: list[str] = []
	page_errors: list[str] = []

	anon_page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
	anon_page.on("pageerror", lambda exc: page_errors.append(str(exc)))

	anon_page.goto(f"{base_url}/server-metrics", wait_until="domcontentloaded")
	expect(anon_page.locator('[data-page="metrics"]')).to_be_visible()
	expect(anon_page.locator('[data-metric-kpi="cpu_used"]')).to_have_text("24.00")
	expect(anon_page.locator('[data-metric-kpi="cpu_temp"]')).to_have_text("52.00")

	anon_page.wait_for_timeout(250)
	assert page_errors == []
	assert console_errors == []
