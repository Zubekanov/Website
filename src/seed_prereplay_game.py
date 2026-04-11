#!/usr/bin/env python3
"""Seed a finished pre-recording game into the dev DB for UI testing.

The game has no rows in popugame_moves so the replay page shows the
'no recording' notice alongside the final board state and scorebox.

Run with:
    .venv/bin/python3 src/seed_prereplay_game.py
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sql.psql_interface import PSQLInterface

CODE = "LEGACY"

# Bitmasks
P0_TOKEN = 0b0001
P0_CLAIM = 0b0010
P1_TOKEN = 0b0100
P1_CLAIM = 0b1000

SIZE = 9

def _make_grid():
    g = [[0] * SIZE for _ in range(SIZE)]

    # Scatter some claimed territory and tokens to make it look realistic
    # Player 0 claims top-left quadrant
    for r in range(4):
        for c in range(4):
            g[r][c] = P0_CLAIM
    # Player 1 claims bottom-right quadrant
    for r in range(5, 9):
        for c in range(5, 9):
            g[r][c] = P1_CLAIM
    # A few contested cells in the middle
    g[4][4] = P0_CLAIM
    g[3][5] = P1_CLAIM
    g[5][3] = P0_CLAIM

    # Place some tokens
    g[1][1] = P0_TOKEN | P0_CLAIM
    g[2][2] = P0_TOKEN | P0_CLAIM
    g[1][2] = P0_TOKEN | P0_CLAIM   # triggers a horizontal claim line

    g[7][7] = P1_TOKEN | P1_CLAIM
    g[6][7] = P1_TOKEN | P1_CLAIM
    g[7][6] = P1_TOKEN | P1_CLAIM

    return g

def main():
    interface = PSQLInterface()
    client = interface.client

    # Remove any existing row with this code so re-runs are idempotent
    existing, _ = client.get_rows_with_filters(
        "popugame_sessions", equalities={"code": CODE}, page_limit=1, page_num=0
    )
    if existing:
        interface.execute_query(
            "DELETE FROM popugame_sessions WHERE code = %s;", (CODE,)
        )
        print(f"Removed existing session {CODE!r}.")

    grid = _make_grid()

    # Count claimed cells for summary
    p0_cells = sum(1 for row in grid for c in row if c & P0_CLAIM)
    p1_cells = sum(1 for row in grid for c in row if c & P1_CLAIM)
    winner = 0 if p0_cells > p1_cells else (1 if p1_cells > p0_cells else None)

    row = client.insert_row("popugame_sessions", {
        "code": CODE,
        "status": "finished",
        "grid_size": SIZE,
        "turn_limit": 40,
        "turn": 40,
        "active_player": 0,
        "grid_state": json.dumps(grid),
        "state_version": 40,
        "player0_name": "Alice",
        "player1_name": "Bob",
        "winner": winner,
        "ended_reason": "turn_limit",
        "is_public": False,
        "is_casual": True,
        "is_members_only": False,
        "ratings_applied": False,
    })

    print(f"Inserted pre-recording game: code={CODE!r}, winner={winner}, "
          f"p0={p0_cells} cells, p1={p1_cells} cells")
    print(f"View at: /popugame/replay/{CODE}")

if __name__ == "__main__":
    sys.exit(main() or 0)
