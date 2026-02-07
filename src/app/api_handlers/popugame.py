from __future__ import annotations

import json
import time
import secrets
import math
from datetime import datetime, timezone

import flask
from flask import Response, stream_with_context

from app.api_context import ApiContext
from app.api_common import get_request_user
from util.popugame.engine import (
	POPUGAME_DEFAULT_SIZE,
	POPUGAME_TURN_LIMIT,
	apply_move,
	is_legal_move,
	make_grid,
	scores,
)

_ANON_PREFIX = "anon:"
_DEFAULT_ELO = 1200
_ELO_K = 24


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/popugame/create", methods=["POST"])
	def api_popugame_create():
		data = flask.request.json or {}
		user = get_request_user(ctx)
		guest_name = None
		if not user:
			guest_name = _normalize_guest_name(data.get("guest_name", ""))
			if not guest_name:
				guest_name = _anonymous_guest_token()

		code = _generate_popugame_code(ctx)
		grid = make_grid(POPUGAME_DEFAULT_SIZE, 0)

		player0_name = guest_name
		player0_user_id = None
		if user:
			player0_user_id = user.get("id")
			player0_name = f"{user.get('first_name','')} {user.get('last_name','')}".strip() or user.get("email") or "Player 1"

		try:
			row = ctx.interface.client.insert_row("popugame_sessions", {
				"code": code,
				"status": "waiting",
				"grid_size": POPUGAME_DEFAULT_SIZE,
				"turn_limit": POPUGAME_TURN_LIMIT,
				"turn": 0,
				"active_player": 0,
				"grid_state": json.dumps(grid),
				"player0_user_id": player0_user_id,
				"player0_name": player0_name,
			})
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 500

		return flask.jsonify({
			"ok": True,
			"code": code,
			"player": 0,
			"state": _popu_state_payload(row, ctx=ctx),
		}), 200

	@api.route("/api/popugame/join", methods=["POST"])
	def api_popugame_join():
		data = flask.request.json or {}
		code = (data.get("code") or "").strip().upper()
		if not code or len(code) != 6 or not code.isalnum():
			return flask.jsonify({
				"ok": False,
				"invalid_link": True,
				"redirect_url": "/popugame/invalid",
				"message": "Invalid game code.",
			}), 400

		row = _get_session_by_code(ctx, code)
		if not row:
			return flask.jsonify({
				"ok": False,
				"invalid_link": True,
				"redirect_url": "/popugame/invalid",
				"message": "Game not found.",
			}), 404

		user = get_request_user(ctx)
		guest_name = None
		if not user:
			guest_name = _normalize_guest_name(data.get("guest_name", ""))
			if not guest_name:
				guest_name = _anonymous_guest_token()

		player = _resolve_join_player(row, user, guest_name)
		if player is None:
			update, player = _build_join_update(row, user, guest_name)
			if player is None:
				if (row.get("status") or "waiting") != "waiting":
					return flask.jsonify({
						"ok": False,
						"invalid_link": True,
						"redirect_url": "/popugame/invalid",
						"message": "Game already in progress.",
					}), 403
				return flask.jsonify({"ok": False, "message": "Game is full."}), 409
			if update:
				p0_name = update.get("player0_name") or row.get("player0_name")
				p1_name = update.get("player1_name") or row.get("player1_name")
				if p0_name and p1_name:
					update["status"] = "active"
				else:
					update["status"] = row.get("status") or "waiting"
				try:
					updated = ctx.interface.execute_query(
						"UPDATE popugame_sessions SET "
						"player0_user_id = COALESCE(%s, player0_user_id), "
						"player1_user_id = COALESCE(%s, player1_user_id), "
						"player0_name = COALESCE(%s, player0_name), "
						"player1_name = COALESCE(%s, player1_name), "
						"status = %s, updated_at = now(), state_version = state_version + 1 "
						"WHERE code = %s RETURNING *;",
						(
							update.get("player0_user_id"),
							update.get("player1_user_id"),
							update.get("player0_name"),
							update.get("player1_name"),
							update.get("status"),
							code,
						),
					) or []
					if updated:
						row = updated[0]
					else:
						row = _get_session_by_code(ctx, code) or row
				except Exception as e:
					return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 500

		return flask.jsonify({
			"ok": True,
			"code": code,
			"player": player,
			"state": _popu_state_payload(row, ctx=ctx),
		}), 200

	@api.route("/api/popugame/state/<code>")
	def api_popugame_state(code: str):
		code = (code or "").strip().upper()
		if not code or len(code) != 6 or not code.isalnum():
			return flask.jsonify({"ok": False, "message": "Invalid game code."}), 400
		row = _get_session_by_code(ctx, code)
		if not row:
			return flask.jsonify({"ok": False, "message": "Game not found."}), 404
		row = _maybe_apply_elo_for_finished_game(ctx, row)
		return flask.jsonify({"ok": True, "state": _popu_state_payload(row, ctx=ctx)}), 200

	@api.route("/api/popugame/move", methods=["POST"])
	def api_popugame_move():
		data = flask.request.json or {}
		code = (data.get("code") or "").strip().upper()
		if not code or len(code) != 6 or not code.isalnum():
			return flask.jsonify({"ok": False, "message": "Invalid game code."}), 400

		row = _get_session_by_code(ctx, code)
		if not row:
			return flask.jsonify({"ok": False, "message": "Game not found."}), 404

		user = get_request_user(ctx)
		player = _resolve_session_player(row, user, data.get("guest_name", ""))
		if player is None:
			return flask.jsonify({"ok": False, "message": "You are not part of this game."}), 403

		status = row.get("status") or "waiting"
		if status == "waiting":
			return flask.jsonify({"ok": False, "message": "Waiting for another player to join."}), 409
		if status == "finished":
			return flask.jsonify({"ok": False, "message": "Game already finished."}), 409

		active_player = int(row.get("active_player") or 0)
		if player != active_player:
			return flask.jsonify({"ok": False, "message": "Not your turn."}), 409

		row_i = int(data.get("row", -1))
		col_i = int(data.get("col", -1))
		size = int(row.get("grid_size") or POPUGAME_DEFAULT_SIZE)
		if row_i < 0 or col_i < 0 or row_i >= size or col_i >= size:
			return flask.jsonify({"ok": False, "message": "Move out of bounds."}), 400

		grid = _parse_grid(row.get("grid_state"), size)
		if not is_legal_move(grid, player, row_i, col_i):
			return flask.jsonify({"ok": False, "message": "Illegal move."}), 400

		apply_move(grid, size, player, row_i, col_i)
		turn = int(row.get("turn") or 0) + 1
		turn_limit = int(row.get("turn_limit") or POPUGAME_TURN_LIMIT)
		next_player = 1 - player
		new_status = status
		winner = row.get("winner")
		ended_reason = row.get("ended_reason")

		if turn >= turn_limit:
			p0, p1 = scores(grid)
			new_status = "finished"
			if p0 > p1:
				winner = 0
			elif p1 > p0:
				winner = 1
			else:
				winner = None
			ended_reason = "turn_limit"

		try:
			ctx.interface.execute_query(
				"UPDATE popugame_sessions SET grid_state = %s, turn = %s, active_player = %s, "
				"status = %s, winner = %s, ended_reason = %s, last_move_at = now(), updated_at = now(), "
				"state_version = state_version + 1 WHERE code = %s RETURNING *;",
				(json.dumps(grid), turn, next_player, new_status, winner, ended_reason, code),
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 500

		row = _get_session_by_code(ctx, code)
		row = _maybe_apply_elo_for_finished_game(ctx, row)
		return flask.jsonify({
			"ok": True,
			"state": _popu_state_payload(row, ctx=ctx),
		}), 200

	@api.route("/api/popugame/stream/<code>")
	def api_popugame_stream(code: str):
		code = (code or "").strip().upper()
		if not code or len(code) != 6 or not code.isalnum():
			return flask.jsonify({"ok": False, "message": "Invalid game code."}), 400

		try:
			last_seen = int(flask.request.args.get("since", "0"))
		except Exception:
			last_seen = 0

		def event(data: dict, event_name: str = "state") -> str:
			return f"event: {event_name}\ndata: {json.dumps(data)}\n\n"

		@stream_with_context
		def generate():
			state = {"last_seen": last_seen}
			start = time.time()
			while time.time() - start < 30:
				row = _get_session_by_code(ctx, code)
				if not row:
					yield event({"ok": False, "message": "Game not found."}, event_name="error")
					return
				row = _maybe_apply_elo_for_finished_game(ctx, row)
				version = int(row.get("state_version") or 0)
				if version != state["last_seen"]:
					payload = _popu_state_payload(row, ctx=ctx)
					yield event({"ok": True, "state": payload})
					state["last_seen"] = version
				time.sleep(1)
			yield event({"ok": True, "ping": True}, event_name="ping")

		resp = Response(generate(), mimetype="text/event-stream")
		resp.headers["Cache-Control"] = "no-cache"
		resp.headers["X-Accel-Buffering"] = "no"
		return resp

	@api.route("/api/popugame/concede", methods=["POST"])
	def api_popugame_concede():
		data = flask.request.json or {}
		code = (data.get("code") or "").strip().upper()
		if not code or len(code) != 6 or not code.isalnum():
			return flask.jsonify({"ok": False, "message": "Invalid game code."}), 400

		row = _get_session_by_code(ctx, code)
		if not row:
			return flask.jsonify({"ok": False, "message": "Game not found."}), 404

		user = get_request_user(ctx)
		player = _resolve_session_player(row, user, data.get("guest_name", ""))
		if player is None:
			return flask.jsonify({"ok": False, "message": "You are not part of this game."}), 403
		if (row.get("status") or "waiting") == "finished":
			return flask.jsonify({"ok": False, "message": "Game already finished."}), 409

		try:
			ctx.interface.execute_query(
				"UPDATE popugame_sessions SET status = %s, winner = %s, ended_reason = %s, "
				"updated_at = now(), state_version = state_version + 1 WHERE code = %s RETURNING *;",
				("finished", 1 - player, "concede", code),
			)
		except Exception as e:
			return flask.jsonify({"ok": False, "message": "Request failed. Please try again."}), 500

		row = _get_session_by_code(ctx, code)
		row = _maybe_apply_elo_for_finished_game(ctx, row)
		return flask.jsonify({
			"ok": True,
			"state": _popu_state_payload(row, ctx=ctx),
		}), 200


def _get_session_by_code(ctx: ApiContext, code: str) -> dict | None:
	rows, _ = ctx.interface.client.get_rows_with_filters(
		"popugame_sessions",
		equalities={"code": code},
		page_limit=1,
		page_num=0,
	)
	return rows[0] if rows else None


def _normalize_guest_name(name: str) -> str | None:
	n = (name or "").strip()
	if not n:
		return None
	return n[:64]

def _anonymous_guest_token() -> str:
	return f"{_ANON_PREFIX}{secrets.token_urlsafe(8)[:12]}"

def _public_player_name(name: object) -> str:
	n = (name or "").strip() if isinstance(name, str) else ""
	if not n:
		return ""
	if n.startswith(_ANON_PREFIX):
		return "Anonymous"
	return n


def _generate_popugame_code(ctx: ApiContext) -> str:
	code_chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
	for _ in range(20):
		code = "".join(secrets.choice(code_chars) for _ in range(6))
		if not _get_session_by_code(ctx, code):
			return code
	raise RuntimeError("Failed to generate unique game code.")


def _parse_grid(grid_state: object, size: int) -> list[list[int]]:
	grid = grid_state
	if isinstance(grid, str):
		try:
			grid = json.loads(grid)
		except Exception:
			grid = []
	if not grid or len(grid) != size:
		grid = make_grid(size, 0)
	return grid


def _popu_state_payload(row: dict, ctx: ApiContext | None = None) -> dict:
	grid = _parse_grid(row.get("grid_state"), int(row.get("grid_size") or POPUGAME_DEFAULT_SIZE))
	elo_map = _get_player_elos(ctx, row) if ctx else {}
	p0_uid = row.get("player0_user_id")
	p1_uid = row.get("player1_user_id")
	return {
		"code": row.get("code"),
		"status": row.get("status"),
		"grid": grid,
		"grid_size": row.get("grid_size"),
		"turn_limit": row.get("turn_limit"),
		"turn": row.get("turn"),
		"active_player": row.get("active_player"),
		"state_version": row.get("state_version") or 0,
		"player0_name": _public_player_name(row.get("player0_name")),
		"player1_name": _public_player_name(row.get("player1_name")),
		"player0_elo": elo_map.get(str(p0_uid)) if p0_uid else None,
		"player1_elo": elo_map.get(str(p1_uid)) if p1_uid else None,
		"winner": row.get("winner"),
		"ended_reason": row.get("ended_reason"),
		"ratings_applied": bool(row.get("ratings_applied")),
		"elo_delta_p0": row.get("elo_delta_p0"),
		"elo_delta_p1": row.get("elo_delta_p1"),
		"elo_after_p0": row.get("elo_after_p0"),
		"elo_after_p1": row.get("elo_after_p1"),
	}


def _get_player_elos(ctx: ApiContext, row: dict) -> dict[str, int]:
	user_ids = [str(uid) for uid in [row.get("player0_user_id"), row.get("player1_user_id")] if uid]
	if not user_ids:
		return {}
	m = {uid: _DEFAULT_ELO for uid in user_ids}
	try:
		rows, _ = ctx.interface.client.get_rows_with_filters(
			"popugame_ratings",
			raw_conditions=["user_id = ANY(%s)"],
			raw_params=[user_ids],
			page_limit=10,
			page_num=0,
		)
	except Exception:
		return m
	for r in rows:
		uid = str(r.get("user_id"))
		if uid in m:
			m[uid] = int(r.get("elo") or _DEFAULT_ELO)
	missing = [uid for uid in user_ids if uid not in {str(r.get("user_id")) for r in rows}]
	if missing:
		try:
			for uid in missing:
				ctx.interface.execute_query(
					"INSERT INTO popugame_ratings (user_id, elo, games_played, wins, losses, draws, updated_at) "
					"VALUES (%s, %s, 0, 0, 0, 0, now()) "
					"ON CONFLICT (user_id) DO NOTHING;",
					(uid, _DEFAULT_ELO),
				)
		except Exception:
			# Keep request successful even if rating-row bootstrap fails.
			pass
	return m


def _expected_score(ra: float, rb: float) -> float:
	return 1.0 / (1.0 + math.pow(10.0, (rb - ra) / 400.0))


def _maybe_apply_elo_for_finished_game(ctx: ApiContext, row: dict | None) -> dict | None:
	if not row:
		return row
	if (row.get("status") or "") != "finished":
		return row
	uid0 = row.get("player0_user_id")
	uid1 = row.get("player1_user_id")
	if not uid0 or not uid1:
		return row
	if str(uid0) == str(uid1):
		return row
	if bool(row.get("ratings_applied")):
		return row

	elo_map = _get_player_elos(ctx, row)
	r0 = float(elo_map.get(str(uid0), _DEFAULT_ELO))
	r1 = float(elo_map.get(str(uid1), _DEFAULT_ELO))
	winner = row.get("winner")
	if winner == 0:
		s0, s1 = 1.0, 0.0
	elif winner == 1:
		s0, s1 = 0.0, 1.0
	else:
		s0, s1 = 0.5, 0.5
	e0 = _expected_score(r0, r1)
	e1 = _expected_score(r1, r0)
	new0 = int(round(r0 + _ELO_K * (s0 - e0)))
	new1 = int(round(r1 + _ELO_K * (s1 - e1)))
	delta0 = int(new0 - int(r0))
	delta1 = int(new1 - int(r1))

	try:
		ctx.interface.execute_query(
			"INSERT INTO popugame_ratings (user_id, elo, games_played, wins, losses, draws, updated_at) "
			"VALUES (%s, %s, 1, %s, %s, %s, now()) "
			"ON CONFLICT (user_id) DO UPDATE SET "
			"elo = EXCLUDED.elo, "
			"games_played = popugame_ratings.games_played + 1, "
			"wins = popugame_ratings.wins + EXCLUDED.wins, "
			"losses = popugame_ratings.losses + EXCLUDED.losses, "
			"draws = popugame_ratings.draws + EXCLUDED.draws, "
			"updated_at = now();",
			(
				uid0,
				new0,
				1 if s0 == 1.0 else 0,
				1 if s0 == 0.0 else 0,
				1 if s0 == 0.5 else 0,
			),
		)
		ctx.interface.execute_query(
			"INSERT INTO popugame_ratings (user_id, elo, games_played, wins, losses, draws, updated_at) "
			"VALUES (%s, %s, 1, %s, %s, %s, now()) "
			"ON CONFLICT (user_id) DO UPDATE SET "
			"elo = EXCLUDED.elo, "
			"games_played = popugame_ratings.games_played + 1, "
			"wins = popugame_ratings.wins + EXCLUDED.wins, "
			"losses = popugame_ratings.losses + EXCLUDED.losses, "
			"draws = popugame_ratings.draws + EXCLUDED.draws, "
			"updated_at = now();",
			(
				uid1,
				new1,
				1 if s1 == 1.0 else 0,
				1 if s1 == 0.0 else 0,
				1 if s1 == 0.5 else 0,
			),
		)
		ctx.interface.execute_query(
			"UPDATE popugame_sessions SET "
			"ratings_applied = TRUE, "
			"elo_before_p0 = %s, elo_after_p0 = %s, elo_delta_p0 = %s, "
			"elo_before_p1 = %s, elo_after_p1 = %s, elo_delta_p1 = %s "
			"WHERE code = %s;",
			(int(r0), new0, delta0, int(r1), new1, delta1, row.get("code")),
		)
		row["ratings_applied"] = True
		row["elo_before_p0"] = int(r0)
		row["elo_after_p0"] = new0
		row["elo_delta_p0"] = delta0
		row["elo_before_p1"] = int(r1)
		row["elo_after_p1"] = new1
		row["elo_delta_p1"] = delta1
	except Exception:
		# If ratings fail, game state should still be returned.
		return row

	return row


def _resolve_join_player(row: dict, user: dict | None, guest_name: str | None) -> int | None:
	if user:
		if row.get("player0_user_id") == user.get("id"):
			return 0
		if row.get("player1_user_id") == user.get("id"):
			return 1
	else:
		if row.get("player0_user_id") is None and row.get("player0_name") == guest_name:
			return 0
		if row.get("player1_user_id") is None and row.get("player1_name") == guest_name:
			return 1
	return None


def _build_join_update(row: dict, user: dict | None, guest_name: str | None) -> tuple[dict, int | None]:
	update = {}
	player = None
	if row.get("player0_user_id") is None and not row.get("player0_name"):
		update["player0_user_id"] = user.get("id") if user else None
		update["player0_name"] = guest_name if not user else (f"{user.get('first_name','')} {user.get('last_name','')}".strip() or user.get("email") or "Player 1")
		player = 0
	elif row.get("player1_user_id") is None and not row.get("player1_name"):
		update["player1_user_id"] = user.get("id") if user else None
		update["player1_name"] = guest_name if not user else (f"{user.get('first_name','')} {user.get('last_name','')}".strip() or user.get("email") or "Player 2")
		player = 1
	return update, player


def _resolve_session_player(row: dict, user: dict | None, guest_name: str) -> int | None:
	if user:
		if row.get("player0_user_id") == user.get("id"):
			return 0
		if row.get("player1_user_id") == user.get("id"):
			return 1
	else:
		guest_norm = _normalize_guest_name(guest_name)
		if guest_norm and row.get("player0_user_id") is None and row.get("player0_name") == guest_norm:
			return 0
		if guest_norm and row.get("player1_user_id") is None and row.get("player1_name") == guest_norm:
			return 1
	return None
