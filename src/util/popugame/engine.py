from __future__ import annotations

from typing import Dict, List, Tuple

# === Game constants ===
POPUGAME_DEFAULT_SIZE = 9
POPUGAME_TURN_LIMIT = 40

P0_TOKEN = 0b0001
P0_CLAIM = 0b0010
P1_TOKEN = 0b0100
P1_CLAIM = 0b1000

GRID_VALUES = {
	0: {"token": P0_TOKEN, "claim": P0_CLAIM},
	1: {"token": P1_TOKEN, "claim": P1_CLAIM},
}


def make_grid(size: int, value: int = 0) -> List[List[int]]:
	return [[value for _ in range(size)] for _ in range(size)]


def out_of_bounds(size: int, row: int, col: int) -> bool:
	return row < 0 or row >= size or col < 0 or col >= size


def check_line(
	grid: List[List[int]],
	size: int,
	mask: int,
	start: Tuple[int, int],
	end: Tuple[int, int],
	step: Tuple[int, int],
) -> Dict[str, object]:
	max_continuous = 0
	continuous = 0
	max_start = None
	max_end = None
	curr_start = None
	last_mask = False
	row, col = start
	end_row = end[0] + step[0]
	end_col = end[1] + step[1]

	while row != end_row or col != end_col:
		if out_of_bounds(size, row, col):
			break
		cell = (grid[row][col] & mask) != 0
		if cell:
			if last_mask:
				continuous += 1
			else:
				curr_start = (row, col)
				continuous = 1
			last_mask = True
		else:
			if continuous > max_continuous:
				max_continuous = continuous
				max_start = curr_start
				max_end = (row - step[0], col - step[1])
			continuous = 0
			last_mask = False
		row += step[0]
		col += step[1]

	if continuous > max_continuous:
		max_continuous = continuous
		max_start = curr_start
		max_end = (row - step[0], col - step[1])

	return {"start": max_start, "end": max_end, "continuous": max_continuous}


def modify_claims(
	grid: List[List[int]],
	size: int,
	player: int,
	mark_for_claim: List[List[bool]],
	start: Tuple[int, int] | None,
	step: Tuple[int, int],
) -> None:
	if not start:
		return
	curr = start
	op = 1
	while True:
		if out_of_bounds(size, curr[0], curr[1]) or (grid[curr[0]][curr[1]] & GRID_VALUES[1 - player]["token"]):
			if op > 0:
				op = -1
				curr = start
				continue
			break
		mark_for_claim[curr[0]][curr[1]] = True
		curr = (curr[0] + op * step[0], curr[1] + op * step[1])


def apply_move(grid: List[List[int]], size: int, player: int, row: int, col: int) -> List[List[int]]:
	token = GRID_VALUES[player]["token"]
	grid[row][col] |= token

	mark_for_claim = make_grid(size, False)
	mark_for_remove = make_grid(size, False)

	# Horizontal
	step = (0, 1)
	start = (row, max(0, col - 2))
	end = (row, min(size - 1, col + 2))
	cont = check_line(grid, size, token, start, end, step)
	if cont["continuous"] >= 3 and cont["start"] and cont["end"]:
		for c in range(cont["start"][1], cont["end"][1] + 1):
			mark_for_remove[row][c] = True
		modify_claims(grid, size, player, mark_for_claim, cont["start"], step)

	# Vertical
	step = (1, 0)
	start = (max(0, row - 2), col)
	end = (min(size - 1, row + 2), col)
	cont = check_line(grid, size, token, start, end, step)
	if cont["continuous"] >= 3 and cont["start"] and cont["end"]:
		for r in range(cont["start"][0], cont["end"][0] + 1):
			mark_for_remove[r][col] = True
		modify_claims(grid, size, player, mark_for_claim, cont["start"], step)

	# Diagonal TL-BR
	step = (1, 1)
	candidates = [
		(row - 2, col - 2),
		(row - 1, col - 1),
		(row, col),
		(row + 1, col + 1),
		(row + 2, col + 2),
	]
	candidates = [c for c in candidates if not out_of_bounds(size, c[0], c[1])]
	if candidates:
		start = candidates[0]
		end = candidates[-1]
		cont = check_line(grid, size, token, start, end, step)
		if cont["continuous"] >= 3 and cont["start"] and cont["end"]:
			for r in range(cont["start"][0], cont["end"][0] + 1):
				c = r - cont["start"][0] + cont["start"][1]
				mark_for_remove[r][c] = True
			modify_claims(grid, size, player, mark_for_claim, cont["start"], step)

	# Diagonal TR-BL
	step = (1, -1)
	candidates = [
		(row - 2, col + 2),
		(row - 1, col + 1),
		(row, col),
		(row + 1, col - 1),
		(row + 2, col - 2),
	]
	candidates = [c for c in candidates if not out_of_bounds(size, c[0], c[1])]
	if candidates:
		start = candidates[0]
		end = candidates[-1]
		cont = check_line(grid, size, token, start, end, step)
		if cont["continuous"] >= 3 and cont["start"] and cont["end"]:
			for r in range(cont["start"][0], cont["end"][0] + 1):
				c = cont["start"][1] - (r - cont["start"][0])
				mark_for_remove[r][c] = True
			modify_claims(grid, size, player, mark_for_claim, cont["start"], step)

	# Apply removals and claims
	for r in range(size):
		for c in range(size):
			if mark_for_remove[r][c]:
				grid[r][c] = 0
	opponent_claim = GRID_VALUES[1 - player]["claim"]
	for r in range(size):
		for c in range(size):
			if mark_for_claim[r][c]:
				grid[r][c] &= (0b1111 - opponent_claim)
				grid[r][c] |= GRID_VALUES[player]["claim"]
	return grid


def is_legal_move(grid: List[List[int]], player: int, row: int, col: int) -> bool:
	cell = grid[row][col]
	occupied = (cell & (P0_TOKEN | P1_TOKEN)) != 0
	if occupied:
		return False
	c0 = (cell & P0_CLAIM) != 0
	c1 = (cell & P1_CLAIM) != 0
	return (not c1) if player == 0 else (not c0)


def scores(grid: List[List[int]]) -> Tuple[int, int]:
	p0 = 0
	p1 = 0
	for row in grid:
		for cell in row:
			if cell & P0_CLAIM:
				p0 += 1
			if cell & P1_CLAIM:
				p1 += 1
	return p0, p1
