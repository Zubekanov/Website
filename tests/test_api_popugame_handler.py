from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api_handlers import popugame


# ---------------------------------------------------------------------------
# Fake infrastructure
# ---------------------------------------------------------------------------

class _FakeClient:
	def __init__(self):
		self.sessions = {}

	def get_rows_with_filters(self, table, **kwargs):
		if table == "popugame_ratings":
			return ([], 0)
		if table != "popugame_sessions":
			return ([], 0)
		code = kwargs.get("equalities", {}).get("code")
		row = self.sessions.get(code)
		return ([row], 1) if row else ([], 0)

	def insert_row(self, table, payload):
		if table != "popugame_sessions":
			return dict(payload)
		row = dict(payload)
		row.setdefault("state_version", 0)
		row.setdefault("id", f"id-{row['code']}")
		self.sessions[row["code"]] = row
		return row

	def update_rows_with_filters(self, table, updates, **kwargs):
		if table != "popugame_sessions":
			return 1
		code = kwargs.get("raw_params", [None])[0]
		if code and code in self.sessions:
			self.sessions[code].update(updates)
		return 1


class _FlexInterface:
	"""Multi-purpose interface fake that dispatches on query content."""

	def __init__(self):
		self.client = _FakeClient()
		self.ratings: dict[str, int] = {}
		self.moves_inserts: list[dict] = []
		self.deleted_codes: list[str] = []
		self.leaderboard_rows: list[dict] = []
		self.history_rows: list[dict] = []
		self.replay_moves: list[dict] = []
		self.query_log: list[str] = []

	def execute_query(self, query: str, params=None):
		q = query.strip().upper()
		self.query_log.append(q)

		if "INSERT INTO POPUGAME_MOVES" in q:
			self.moves_inserts.append({"params": params})
			return []

		if "DELETE FROM POPUGAME_SESSIONS" in q:
			code = (params or (None,))[0]
			if code:
				self.deleted_codes.append(code)
				self.client.sessions.pop(code, None)
			return []

		if "INSERT INTO POPUGAME_RATINGS" in q:
			return []

		if "UPDATE POPUGAME_SESSIONS SET RATINGS_APPLIED" in q:
			return []

		if "SELECT PR.ELO" in q:
			return self.leaderboard_rows

		if "FROM POPUGAME_MOVES" in q:
			return self.replay_moves

		if "WHERE STATUS = 'FINISHED'" in q:
			return self.history_rows

		if "UPDATE POPUGAME_SESSIONS" in q:
			code = params[-1] if params else None
			row = self.client.sessions.get(code, {})
			# move update: grid_state, turn, active_player, status, winner, ended_reason, code
			if params and len(params) >= 7:
				row.update({
					"grid_state": params[0],
					"turn": params[1],
					"active_player": params[2],
					"status": params[3],
					"winner": params[4],
					"ended_reason": params[5],
					"state_version": int(row.get("state_version") or 0) + 1,
				})
			# join update: p0_uid, p1_uid, p0_name, p1_name, status, code (6 params)
			elif params and len(params) == 6:
				row.update({
					"player0_user_id": params[0] if params[0] is not None else row.get("player0_user_id"),
					"player1_user_id": params[1] if params[1] is not None else row.get("player1_user_id"),
					"player0_name": params[2] if params[2] is not None else row.get("player0_name"),
					"player1_name": params[3] if params[3] is not None else row.get("player1_name"),
					"status": params[4],
					"state_version": int(row.get("state_version") or 0) + 1,
				})
			# concede update: status, winner, ended_reason, code (4 params)
			elif params and len(params) == 4:
				row.update({
					"status": params[0],
					"winner": params[1],
					"ended_reason": params[2],
					"state_version": int(row.get("state_version") or 0) + 1,
				})
			return [row] if row else []

		if "POPUGAME_RATINGS" in q:
			uid_list = (params or [[]])[0] if params else []
			return [
				{"user_id": uid, "elo": self.ratings.get(uid, 1200)}
				for uid in uid_list
				if uid in self.ratings
			]

		return []


def _waiting_game(code: str, **overrides) -> dict:
	base = {
		"code": code,
		"id": f"id-{code}",
		"status": "waiting",
		"grid_size": 9,
		"turn_limit": 40,
		"turn": 0,
		"active_player": 0,
		"grid_state": json.dumps([[0] * 9 for _ in range(9)]),
		"player0_user_id": None,
		"player1_user_id": None,
		"player0_name": "Host",
		"player1_name": None,
		"winner": None,
		"ended_reason": None,
		"state_version": 0,
		"is_public": False,
		"is_casual": False,
		"is_members_only": False,
		"ratings_applied": False,
		"created_at": datetime.now(timezone.utc),
		"updated_at": datetime.now(timezone.utc),
		"last_move_at": None,
	}
	base.update(overrides)
	return base


def _active_game(code: str, **overrides) -> dict:
	base = _waiting_game(code)
	base.update({
		"status": "active",
		"player1_name": "Opponent",
		"state_version": 1,
	})
	base.update(overrides)
	return base


# ---------------------------------------------------------------------------
# Existing tests (preserved)
# ---------------------------------------------------------------------------

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
	ctx.interface.client.sessions[code] = _active_game(code, player0_name="Host", player1_name="Opponent")
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
	ctx.interface.client.sessions[code] = _active_game(code, player0_name="Host", player1_name="Opponent")
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


# ---------------------------------------------------------------------------
# /create — game flags
# ---------------------------------------------------------------------------

def test_popugame_create_public_flag_stored(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/create", json={"is_public": True})
	assert resp.status_code == 200
	code = resp.get_json()["code"]
	assert ctx.interface.client.sessions[code]["is_public"] is True


def test_popugame_create_casual_flag_stored(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/create", json={"is_casual": True})
	assert resp.status_code == 200
	code = resp.get_json()["code"]
	assert ctx.interface.client.sessions[code]["is_casual"] is True


def test_popugame_create_members_only_flag_stored(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/create", json={"is_members_only": True})
	assert resp.status_code == 200
	code = resp.get_json()["code"]
	assert ctx.interface.client.sessions[code]["is_members_only"] is True


def test_popugame_create_casual_overrides_members_only(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/create", json={"is_casual": True, "is_members_only": True})
	assert resp.status_code == 200
	code = resp.get_json()["code"]
	row = ctx.interface.client.sessions[code]
	assert row["is_casual"] is True
	assert row["is_members_only"] is False


def test_popugame_create_returns_player_0(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/create", json={"guest_name": "Alice"})
	body = resp.get_json()
	assert body["player"] == 0
	assert body["state"]["status"] == "waiting"


# ---------------------------------------------------------------------------
# /join — second player, members-only, re-join
# ---------------------------------------------------------------------------

def test_popugame_join_members_only_rejects_anonymous(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["MBRS01"] = _waiting_game("MBRS01", is_members_only=True)
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/join", json={"code": "MBRS01", "guest_name": "Guest"})
	assert resp.status_code == 403
	body = resp.get_json()
	assert body.get("members_only") is True


def test_popugame_join_second_player_transitions_to_active(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["WAIT01"] = _waiting_game("WAIT01", player0_name="Host")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/join", json={"code": "WAIT01", "guest_name": "Joiner"})
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["player"] == 1
	assert body["state"]["status"] == "active"


def test_popugame_join_existing_player0_gets_slot_back(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["WAIT02"] = _waiting_game("WAIT02", player0_name="Host")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/join", json={"code": "WAIT02", "guest_name": "Host"})
	assert resp.status_code == 200
	assert resp.get_json()["player"] == 0


def test_popugame_join_not_found_returns_404(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/join", json={"code": "NOPE99", "guest_name": "Guest"})
	assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /move — valid move, illegal move, wrong turn
# ---------------------------------------------------------------------------

def test_popugame_move_valid_increments_turn(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["MOVE01"] = _active_game("MOVE01", player0_name="Alice", player1_name="Bob")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post(
		"/api/popugame/move",
		json={"code": "MOVE01", "guest_name": "Alice", "row": 0, "col": 0},
	)
	assert resp.status_code == 200
	state = resp.get_json()["state"]
	assert state["turn"] == 1
	assert state["active_player"] == 1


def test_popugame_move_records_to_moves_table(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["MOVE02"] = _active_game("MOVE02", player0_name="Alice", player1_name="Bob")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	app.test_client().post(
		"/api/popugame/move",
		json={"code": "MOVE02", "guest_name": "Alice", "row": 0, "col": 0},
	)
	assert len(ctx.interface.moves_inserts) == 1


def test_popugame_move_not_your_turn_returns_409(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["MOVE03"] = _active_game(
		"MOVE03", player0_name="Alice", player1_name="Bob", active_player=1
	)
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post(
		"/api/popugame/move",
		json={"code": "MOVE03", "guest_name": "Alice", "row": 0, "col": 0},
	)
	assert resp.status_code == 409
	assert "Not your turn" in resp.get_json()["message"]


def test_popugame_move_out_of_bounds_returns_400(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["MOVE04"] = _active_game("MOVE04", player0_name="Alice", player1_name="Bob")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post(
		"/api/popugame/move",
		json={"code": "MOVE04", "guest_name": "Alice", "row": 99, "col": 0},
	)
	assert resp.status_code == 400


def test_popugame_move_non_player_returns_403(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["MOVE05"] = _active_game("MOVE05", player0_name="Alice", player1_name="Bob")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post(
		"/api/popugame/move",
		json={"code": "MOVE05", "guest_name": "Intruder", "row": 0, "col": 0},
	)
	assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /concede
# ---------------------------------------------------------------------------

def test_popugame_concede_sets_opponent_as_winner(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["CONC01"] = _active_game("CONC01", player0_name="Alice", player1_name="Bob")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/concede", json={"code": "CONC01", "guest_name": "Alice"})
	assert resp.status_code == 200
	state = resp.get_json()["state"]
	assert state["winner"] == 1
	assert state["ended_reason"] == "concede"


def test_popugame_concede_non_player_returns_403(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["CONC02"] = _active_game("CONC02", player0_name="Alice", player1_name="Bob")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/concede", json={"code": "CONC02", "guest_name": "Intruder"})
	assert resp.status_code == 403


def test_popugame_concede_already_finished_returns_409(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["CONC03"] = _active_game(
		"CONC03", status="finished", player0_name="Alice", player1_name="Bob", winner=0
	)
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/concede", json={"code": "CONC03", "guest_name": "Alice"})
	assert resp.status_code == 409


# ---------------------------------------------------------------------------
# /abandon
# ---------------------------------------------------------------------------

def test_popugame_abandon_waiting_game_deletes_it(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["ABND01"] = _waiting_game("ABND01")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/abandon", json={"code": "ABND01"})
	assert resp.status_code == 200
	assert resp.get_json()["ok"] is True
	assert "ABND01" in ctx.interface.deleted_codes


def test_popugame_abandon_active_game_rejected(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["ABND02"] = _active_game("ABND02", player0_name="Alice", player1_name="Bob")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/abandon", json={"code": "ABND02"})
	assert resp.status_code == 409
	assert "already started" in resp.get_json()["message"]


def test_popugame_abandon_not_found_returns_404(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/abandon", json={"code": "NOPE99"})
	assert resp.status_code == 404


def test_popugame_abandon_owner_can_delete_their_game(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["ABND03"] = _waiting_game("ABND03", player0_user_id="user-A")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: {"id": "user-A"})
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/abandon", json={"code": "ABND03"})
	assert resp.status_code == 200
	assert "ABND03" in ctx.interface.deleted_codes


def test_popugame_abandon_wrong_user_rejected(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["ABND04"] = _waiting_game("ABND04", player0_user_id="user-A")
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: {"id": "user-B"})
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/abandon", json={"code": "ABND04"})
	assert resp.status_code == 403


def test_popugame_abandon_invalid_code_returns_400(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().post("/api/popugame/abandon", json={"code": "!bad!"})
	assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /public
# ---------------------------------------------------------------------------

class _PublicInterface(_FlexInterface):
	"""Extension that serves a fixed set of public lobby rows."""

	def __init__(self, public_rows: list[dict]):
		super().__init__()
		self._public_rows = public_rows

	def execute_query(self, query: str, params=None):
		q = query.strip().upper()
		if "WHERE STATUS = 'WAITING' AND IS_PUBLIC = TRUE" in q:
			return self._public_rows
		return super().execute_query(query, params)


def test_popugame_public_returns_waiting_games(monkeypatch, app_factory):
	from datetime import datetime, timezone

	rows = [
		{
			"code": "PUB001",
			"player0_name": "Alice",
			"player0_user_id": None,
			"created_at": datetime.now(timezone.utc),
			"is_casual": False,
			"is_members_only": False,
		},
	]
	ctx = SimpleNamespace(auth_token_name="session", interface=_PublicInterface(rows))
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/public")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert len(body["games"]) == 1
	assert body["games"][0]["code"] == "PUB001"


def test_popugame_public_is_own_game_for_matching_user(monkeypatch, app_factory):
	from datetime import datetime, timezone

	rows = [
		{
			"code": "PUB002",
			"player0_name": "Alice",
			"player0_user_id": "user-A",
			"created_at": datetime.now(timezone.utc),
			"is_casual": False,
			"is_members_only": False,
		},
	]
	ctx = SimpleNamespace(auth_token_name="session", interface=_PublicInterface(rows))
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: {"id": "user-A"})
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/public")
	assert resp.get_json()["games"][0]["is_own_game"] is True


def test_popugame_public_is_not_own_game_for_different_user(monkeypatch, app_factory):
	from datetime import datetime, timezone

	rows = [
		{
			"code": "PUB003",
			"player0_name": "Alice",
			"player0_user_id": "user-A",
			"created_at": datetime.now(timezone.utc),
			"is_casual": False,
			"is_members_only": False,
		},
	]
	ctx = SimpleNamespace(auth_token_name="session", interface=_PublicInterface(rows))
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: {"id": "user-B"})
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/public")
	assert resp.get_json()["games"][0]["is_own_game"] is False


def test_popugame_public_anon_host_name_masked(monkeypatch, app_factory):
	from datetime import datetime, timezone

	rows = [
		{
			"code": "PUB004",
			"player0_name": "anon:abcdef12",
			"player0_user_id": None,
			"created_at": datetime.now(timezone.utc),
			"is_casual": False,
			"is_members_only": False,
		},
	]
	ctx = SimpleNamespace(auth_token_name="session", interface=_PublicInterface(rows))
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/public")
	assert resp.get_json()["games"][0]["host_name"] == "Anonymous"


def test_popugame_public_empty_when_no_games(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_PublicInterface([]))
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/public")
	assert resp.status_code == 200
	assert resp.get_json()["games"] == []


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

class _HistoryInterface(_FlexInterface):
	def __init__(self, history_rows: list[dict]):
		super().__init__()
		self._history_rows_data = history_rows
		self._captured_params: list = []

	def execute_query(self, query: str, params=None):
		q = query.strip().upper()
		if "FROM POPUGAME_SESSIONS" in q and "FINISHED" in q:
			self._captured_params.append(params)
			return self._history_rows_data
		return super().execute_query(query, params)


def test_popugame_history_returns_finished_games(monkeypatch, app_factory):
	from datetime import datetime, timezone

	rows = [
		{
			"code": "HIST01",
			"player0_name": "Alice",
			"player1_name": "Bob",
			"winner": 0,
			"ended_reason": "turn_limit",
			"turn": 40,
			"elo_after_p0": 1212,
			"elo_after_p1": 1188,
			"elo_delta_p0": 12,
			"elo_delta_p1": -12,
			"updated_at": datetime.now(timezone.utc),
		},
	]
	iface = _HistoryInterface(rows)
	ctx = SimpleNamespace(auth_token_name="session", interface=iface)
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/history?limit=10")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert len(body["games"]) == 1
	g = body["games"][0]
	assert g["code"] == "HIST01"
	assert g["winner"] == 0


def test_popugame_history_limit_capped_at_50(monkeypatch, app_factory):
	iface = _HistoryInterface([])
	ctx = SimpleNamespace(auth_token_name="session", interface=iface)
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	app.test_client().get("/api/popugame/history?limit=999")
	# Verify the query was called with limit capped; params captured contain the limit value
	assert iface._captured_params
	limit_used = iface._captured_params[0][0] if iface._captured_params[0] else None
	assert limit_used == 50


# ---------------------------------------------------------------------------
# /leaderboard
# ---------------------------------------------------------------------------

class _LeaderboardInterface(_FlexInterface):
	def __init__(self, lb_rows: list[dict]):
		super().__init__()
		self._lb_rows = lb_rows

	def execute_query(self, query: str, params=None):
		q = query.strip().upper()
		if "FROM POPUGAME_RATINGS PR" in q:
			return self._lb_rows
		return super().execute_query(query, params)


def test_popugame_leaderboard_returns_entries(monkeypatch, app_factory):
	rows = [
		{"elo": 1350, "games_played": 10, "wins": 7, "losses": 2, "draws": 1,
		 "first_name": "Alice", "last_name": "Smith"},
		{"elo": 1200, "games_played": 5, "wins": 3, "losses": 1, "draws": 1,
		 "first_name": "Bob", "last_name": "Jones"},
	]
	ctx = SimpleNamespace(auth_token_name="session", interface=_LeaderboardInterface(rows))
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/leaderboard")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert len(body["entries"]) == 2
	assert body["entries"][0]["elo"] == 1350
	assert body["entries"][0]["name"] == "Alice Smith"


def test_popugame_leaderboard_empty_list_on_no_data(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_LeaderboardInterface([]))
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/leaderboard")
	assert resp.status_code == 200
	assert resp.get_json()["entries"] == []


# ---------------------------------------------------------------------------
# /replay
# ---------------------------------------------------------------------------

def test_popugame_replay_returns_move_list(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["RPLY01"] = _active_game(
		"RPLY01", status="finished", player0_name="Alice", player1_name="Bob", winner=0
	)
	ctx.interface.replay_moves = [
		{
			"move_number": 0, "player": 0, "row_idx": 0, "col_idx": 0,
			"grid_state": json.dumps([[0] * 9 for _ in range(9)]),
		},
		{
			"move_number": 1, "player": 1, "row_idx": 1, "col_idx": 1,
			"grid_state": json.dumps([[0] * 9 for _ in range(9)]),
		},
	]
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/replay/RPLY01")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert len(body["moves"]) == 2
	assert body["no_recording"] is False


def test_popugame_replay_no_recording_when_no_moves(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	ctx.interface.client.sessions["RPLY02"] = _active_game(
		"RPLY02", status="finished", player0_name="Alice", player1_name="Bob", winner=1
	)
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/replay/RPLY02")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["moves"] == []
	assert body["no_recording"] is True


def test_popugame_replay_invalid_code_returns_400(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/replay/bad!")
	assert resp.status_code == 400


def test_popugame_replay_not_found_returns_404(monkeypatch, app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=_FlexInterface())
	monkeypatch.setattr(popugame, "get_request_user", lambda ctx: None)
	app = app_factory(popugame.register, ctx)

	resp = app.test_client().get("/api/popugame/replay/NOPE99")
	assert resp.status_code == 404
