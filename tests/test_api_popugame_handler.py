from __future__ import annotations

import json
from types import SimpleNamespace

from app.api_handlers import popugame


class _FakeClient:
	def __init__(self):
		self.sessions = {}

	def get_rows_with_filters(self, table, **kwargs):
		if table != "popugame_sessions":
			return ([], 0)
		code = kwargs.get("equalities", {}).get("code")
		row = self.sessions.get(code)
		return ([row], 1) if row else ([], 0)

	def insert_row(self, table, payload):
		row = dict(payload)
		row.setdefault("state_version", 0)
		self.sessions[row["code"]] = row
		return row

	def update_rows_with_filters(self, table, updates, **kwargs):
		code = kwargs["raw_params"][0]
		self.sessions[code].update(updates)
		return 1


class _FakeInterface:
	def __init__(self):
		self.client = _FakeClient()

	def execute_query(self, query, params):
		code = params[-1]
		row = self.client.sessions[code]
		row.update(
			{
				"grid_state": params[0],
				"turn": params[1],
				"active_player": params[2],
				"status": params[3],
				"winner": params[4],
				"ended_reason": params[5],
				"state_version": int(row.get("state_version") or 0) + 1,
			}
		)
		return [row]


def test_popugame_create_allows_anonymous_without_guest_name(app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FakeInterface())
	app = app_factory(popugame.register, ctx)
	client = app.test_client()

	resp = client.post("/api/popugame/create", json={})
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert body["state"]["player0_name"] == "Anonymous"


def test_popugame_create_guest_success(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FakeInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)

	app = app_factory(popugame.register, ctx)
	client = app.test_client()
	resp = client.post("/api/popugame/create", json={"guest_name": "Guest A"})

	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert len(body["code"]) == 6
	assert body["state"]["status"] == "waiting"


def test_popugame_move_rejects_when_waiting(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FakeInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)

	app = app_factory(popugame.register, ctx)
	client = app.test_client()
	create_resp = client.post("/api/popugame/create", json={"guest_name": "Guest A"})
	code = create_resp.get_json()["code"]

	resp = client.post("/api/popugame/move", json={"code": code, "guest_name": "Guest A", "row": 0, "col": 0})
	assert resp.status_code == 409
	assert "Waiting for another player" in resp.get_json()["message"]


def test_popugame_join_started_game_without_guest_name_returns_invalid_link(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FakeInterface())
	code = "ABC123"
	ctx.interface.client.sessions[code] = {
		"code": code,
		"status": "active",
		"grid_size": 9,
		"turn_limit": 40,
		"turn": 3,
		"active_player": 1,
		"grid_state": json.dumps([[0 for _ in range(9)] for _ in range(9)]),
		"player0_user_id": None,
		"player1_user_id": None,
		"player0_name": "Host",
		"player1_name": "Opponent",
		"state_version": 1,
	}
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)

	app = app_factory(popugame.register, ctx)
	client = app.test_client()
	resp = client.post("/api/popugame/join", json={"code": code})

	assert resp.status_code == 403
	body = resp.get_json()
	assert body["invalid_link"] is True
	assert body["redirect_url"] == "/popugame/invalid"


def test_popugame_join_started_game_unknown_guest_returns_invalid_link(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FakeInterface())
	code = "ABC123"
	ctx.interface.client.sessions[code] = {
		"code": code,
		"status": "active",
		"grid_size": 9,
		"turn_limit": 40,
		"turn": 3,
		"active_player": 1,
		"grid_state": json.dumps([[0 for _ in range(9)] for _ in range(9)]),
		"player0_user_id": None,
		"player1_user_id": None,
		"player0_name": "Host",
		"player1_name": "Opponent",
		"state_version": 1,
	}
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)

	app = app_factory(popugame.register, ctx)
	client = app.test_client()
	resp = client.post("/api/popugame/join", json={"code": code, "guest_name": "Intruder"})

	assert resp.status_code == 403
	body = resp.get_json()
	assert body["invalid_link"] is True
	assert body["redirect_url"] == "/popugame/invalid"


def test_popugame_state_invalid_code(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FakeInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)
	client = app.test_client()

	resp = client.get("/api/popugame/state/not-valid")
	assert resp.status_code == 400
	assert resp.get_json()["message"] == "Invalid game code."


def test_popugame_helper_functions_cover_edge_cases():
	assert popugame._normalize_guest_name(" a ") == "a"
	assert popugame._normalize_guest_name("  Alice  ") == "Alice"
	assert popugame._normalize_guest_name("x" * 100) == "x" * 64

	row = {"player0_user_id": None, "player1_user_id": None, "player0_name": "Host", "player1_name": None}
	assert popugame._resolve_join_player(row, None, "Host") == 0
	assert popugame._resolve_join_player(row, {"id": "u1"}, None) is None

	update, player = popugame._build_join_update(
		{"player0_user_id": None, "player1_user_id": None, "player0_name": "Host", "player1_name": None},
		None,
		"Guest B",
	)
	assert player == 1
	assert update["player1_name"] == "Guest B"

	grid = popugame._parse_grid("not-json", 3)
	assert len(grid) == 3
	assert len(grid[0]) == 3

	state = popugame._popu_state_payload({
		"code": "ABC123",
		"status": "active",
		"grid_size": 3,
		"grid_state": "not-json",
		"turn_limit": 10,
		"turn": 1,
		"active_player": 1,
		"state_version": None,
		"player0_name": "A",
		"player1_name": "B",
		"winner": None,
		"ended_reason": None,
	})
	assert state["code"] == "ABC123"
	assert state["state_version"] == 0
	assert len(state["grid"]) == 3
	assert popugame._public_player_name("anon:abcdef") == "Anonymous"
