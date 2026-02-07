from __future__ import annotations

from util.popugame.engine import (
	P0_CLAIM,
	P0_TOKEN,
	P1_CLAIM,
	apply_move,
	is_legal_move,
	make_grid,
	scores,
)


def test_is_legal_move_rejects_occupied_and_opponent_claim():
	grid = make_grid(3, 0)
	grid[0][0] = P0_TOKEN
	grid[0][1] = P1_CLAIM

	assert not is_legal_move(grid, 1, 0, 0)
	assert not is_legal_move(grid, 0, 0, 1)


def test_apply_move_claims_and_clears_three_in_row():
	grid = make_grid(5, 0)
	grid[2][1] = P0_TOKEN
	grid[2][2] = P0_TOKEN

	apply_move(grid, 5, 0, 2, 3)

	assert grid[2][1] & P0_TOKEN == 0
	assert grid[2][2] & P0_TOKEN == 0
	assert grid[2][3] & P0_TOKEN == 0
	assert grid[2][2] & P0_CLAIM != 0


def test_scores_counts_claim_cells():
	grid = make_grid(2, 0)
	grid[0][0] = P0_CLAIM
	grid[1][1] = P1_CLAIM

	assert scores(grid) == (1, 1)
