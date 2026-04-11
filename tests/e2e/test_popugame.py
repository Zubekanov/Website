from __future__ import annotations

import re
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e


def _enabled_cell_count(page) -> int:
	return page.locator("[data-popugame-board] .popugame__cell:not([disabled])").count()


def _wait_for_active_turn(page_a, page_b, *, timeout_s: float = 10.0):
	deadline = time.time() + timeout_s
	while time.time() < deadline:
		if _enabled_cell_count(page_a) > 0:
			return page_a
		if _enabled_cell_count(page_b) > 0:
			return page_b
		page_a.wait_for_timeout(100)
	raise AssertionError("No PopuGame player received an active turn in time.")


def test_popugame_local_smoke(anon_page, base_url: str) -> None:
	anon_page.goto(f"{base_url}/popugame", wait_until="domcontentloaded")
	expect(anon_page.get_by_role("button", name="Rules")).to_be_visible()
	expect(anon_page.locator("[data-popugame-turn]")).to_have_text("40")

	anon_page.get_by_role("button", name="Rules").click()
	rules_dialog = anon_page.get_by_role("dialog", name="PopuGame Rules")
	expect(rules_dialog.locator("#popugame-rules-title")).to_be_visible()
	rules_dialog.locator("[data-popugame-close]").evaluate("(element) => element.click()")
	expect(rules_dialog).to_be_hidden()

	first_cell = anon_page.locator("[data-popugame-board] .popugame__cell").first
	first_cell.click()
	expect(anon_page.locator("[data-popugame-turn]")).to_have_text("39")
	expect(anon_page.locator("[data-popugame-undo]")).to_be_enabled()

	anon_page.locator("[data-popugame-undo]").click()
	expect(anon_page.locator("[data-popugame-turn]")).to_have_text("40")

	anon_page.locator("[data-popugame-reset]").click()
	expect(anon_page.locator("[data-popugame-turn]")).to_have_text("40")


def test_popugame_multiplayer_flow(context_factory, base_url: str) -> None:
	host_context = context_factory("anonymous")
	join_context = context_factory("anonymous")
	host_page = host_context.new_page()
	join_page = join_context.new_page()

	host_page.goto(f"{base_url}/popugame", wait_until="domcontentloaded")
	host_page.locator("[data-popugame-host]").click()
	host_page.wait_for_url(re.compile(rf"{re.escape(base_url)}/popugame/[A-Z0-9]{{6}}$"))
	game_url = host_page.url
	expect(host_page.locator("[data-popugame-status]")).to_contain_text("Waiting for opponent")

	join_page.goto(game_url, wait_until="domcontentloaded")
	expect(host_page.locator("[data-popugame-status]")).not_to_contain_text("Waiting for opponent", timeout=10000)
	expect(join_page.locator("[data-popugame-status]")).not_to_contain_text("Waiting for opponent", timeout=10000)

	first_mover = _wait_for_active_turn(host_page, join_page)
	second_mover = join_page if first_mover is host_page else host_page

	first_mover.locator("[data-popugame-board] .popugame__cell:not([disabled])").first.click()
	_wait_for_active_turn(second_mover, first_mover)
	second_mover.locator("[data-popugame-board] .popugame__cell:not([disabled])").first.click()

	second_mover.locator("[data-popugame-concede]").click()
	second_mover.locator("[data-popugame-dialog-confirm]").click()

	expect(host_page.locator("[data-popugame-endgame-result]")).to_be_visible(timeout=10000)
	expect(join_page.locator("[data-popugame-endgame-result]")).to_be_visible(timeout=10000)
	expect(host_page.locator("[data-popugame-endgame-reason]")).to_contain_text("conceded")
	expect(join_page.locator("[data-popugame-endgame-reason]")).to_contain_text("conceded")
