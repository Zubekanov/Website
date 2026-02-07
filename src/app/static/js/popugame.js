(() => {
	const root = document.querySelector("[data-popugame]");
	if (!root) return;

	const size = Number.parseInt(root.dataset.size || "9", 10);
	const turnLimit = Number.parseInt(root.dataset.turnLimit || "40", 10);

	const boardEl = root.querySelector("[data-popugame-board]");
	const statusEl = root.querySelector("[data-popugame-status]");
	const turnEl = root.querySelector("[data-popugame-turn]");
	const turnTrackEl = root.querySelector("[data-popugame-turn-track]");
	const score0El = root.querySelector("[data-popugame-score=\"0\"]");
	const score1El = root.querySelector("[data-popugame-score=\"1\"]");
	const name0El = root.querySelector("[data-popugame-name=\"0\"]");
	const name1El = root.querySelector("[data-popugame-name=\"1\"]");
	const sharePanelEl = root.querySelector("[data-popugame-share-panel]");
	const resetBtn = root.querySelector("[data-popugame-reset]");
	const undoBtn = root.querySelector("[data-popugame-undo]");
	const rulesBtn = root.querySelector("[data-popugame-rules]");
	const hostBtn = root.querySelector("[data-popugame-host]");
	const joinBtn = root.querySelector("[data-popugame-join]");
	const concedeBtn = root.querySelector("[data-popugame-concede]");
	const postgameButtons = root.querySelectorAll("[data-popugame-postgame]");
	const modalEl = root.querySelector("[data-popugame-modal]");
	const closeBtn = root.querySelector("[data-popugame-close]");
	const dialogEl = root.querySelector("[data-popugame-dialog]");
	const dialogTitleEl = root.querySelector("[data-popugame-dialog-title]");
	const dialogBodyEl = root.querySelector("[data-popugame-dialog-body]");
	const dialogCloseEl = root.querySelector("[data-popugame-dialog-close]");
	const dialogCancelEl = root.querySelector("[data-popugame-dialog-cancel]");
	const dialogConfirmEl = root.querySelector("[data-popugame-dialog-confirm]");
	const endgameEl = root.querySelector("[data-popugame-endgame]");
	const endgameResultEl = root.querySelector("[data-popugame-endgame-result]");
	const endgameScoreEl = root.querySelector("[data-popugame-endgame-score]");
	const endgameReasonEl = root.querySelector("[data-popugame-endgame-reason]");
	const endgameEloEl = root.querySelector("[data-popugame-endgame-elo]");
	const endgameEloP0El = root.querySelector("[data-popugame-endgame-elo-p0]");
	const endgameEloP1El = root.querySelector("[data-popugame-endgame-elo-p1]");
	const endgameCloseEl = root.querySelector("[data-popugame-endgame-close]");
	const endgameDismissEl = root.querySelector("[data-popugame-endgame-dismiss]");
	const endgamePlayAgainEl = root.querySelector("[data-popugame-endgame-playagain]");
	const endgameHostEl = root.querySelector("[data-popugame-endgame-host]");
	const endgameJoinEl = root.querySelector("[data-popugame-endgame-join]");

	const noClaim = 0b0000;
	const p0Token = 0b0001;
	const p0Claim = 0b0010;
	const p1Token = 0b0100;
	const p1Claim = 0b1000;

	const gridValues = [
		{ token: p0Token, claim: p0Claim },
		{ token: p1Token, claim: p1Claim },
	];

	let grid = [];
	let turn = 0;
	let player = 0;
	let legalMoves = [];
	let gameOver = false;
	let gameStatus = "local";
	let winner = null;
	let stateVersion = 0;
	let playerIndex = null;
	let previousActivePlayer = null;
	let eventSource = null;
	let moveHistory = [];
	let windowFocused = typeof document.hasFocus === "function" ? document.hasFocus() : true;
	let player0Name = "Player 1";
	let player1Name = "Player 2";
	let endedReason = null;
	let ratingsApplied = false;
	let eloDeltaP0 = null;
	let eloDeltaP1 = null;
	let player0Elo = null;
	let player1Elo = null;
	let shownEndgameToken = null;
	let overlayOpenCount = 0;
	let scrollLockY = 0;
	let gameCode = (root.dataset.popugameCode || "").trim().toUpperCase();
	if (!gameCode) {
		const parts = window.location.pathname.split("/").filter(Boolean);
		if (parts.length === 2 && parts[0] === "popugame" && /^[A-Z0-9]{6}$/.test(parts[1].toUpperCase())) {
			gameCode = parts[1].toUpperCase();
		}
	}
	const storageSuffix = gameCode ? gameCode.toLowerCase() : "local";
	const STORAGE_KEY = `popugameState:${storageSuffix}`;
	const HISTORY_KEY = `popugameHistory:${storageSuffix}`;
	const GUEST_KEY = `popugameGuestToken:${storageSuffix}`;
	const PENDING_GUEST_KEY = "popugameGuestTokenPending";
	const isMultiplayer = Boolean(gameCode);
	const inviteLink = gameCode ? `${window.location.origin}/popugame/${gameCode}` : "";

	const updateShareFields = () => {
		if (!gameCode) return;
		const linkEls = root.querySelectorAll("[data-popugame-share-link]");
		const codeEls = root.querySelectorAll("[data-popugame-share-code]");
		linkEls.forEach((el) => { el.textContent = inviteLink; });
		codeEls.forEach((el) => { el.textContent = gameCode; });
		const chips = root.querySelectorAll("[data-popugame-copy-chip]");
		chips.forEach((chip) => {
			const kind = chip.dataset.popugameCopyKind;
			chip.dataset.copyText = kind === "code" ? gameCode : inviteLink;
		});
	};

	const formatPlayerName = (name, elo, fallback) => {
		const base = (name || fallback || "").trim() || fallback;
		if (typeof elo !== "number" || Number.isNaN(elo)) return base;
		return `${base} (${elo})`;
	};

	const basePlayerName = (name, fallback) => (
		(name || fallback || "").trim() || fallback
	);

	const humanEndReason = (reason) => {
		if (reason === "concede") return "concede";
		if (reason === "turn_limit") return "turn limit";
		return "completed";
	};

	const formatDelta = (delta) => {
		if (delta === null || delta === undefined) return null;
		const n = Number(delta);
		if (!Number.isFinite(n)) return null;
		const rounded = Math.trunc(n);
		if (!Number.isFinite(rounded)) return null;
		if (rounded === 0 && delta !== 0 && delta !== "0") return null;
		return rounded >= 0 ? `+${rounded}` : String(rounded);
	};

	const escapeHtml = (value) => String(value)
		.replaceAll("&", "&amp;")
		.replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;")
		.replaceAll('"', "&quot;")
		.replaceAll("'", "&#39;");

	if (name0El) player0Name = basePlayerName(name0El.textContent, "Player 1");
	if (name1El) player1Name = basePlayerName(name1El.textContent, "Player 2");

	const lockPageScroll = () => {
		scrollLockY = window.scrollY || window.pageYOffset || 0;
		document.body.style.position = "fixed";
		document.body.style.top = `-${scrollLockY}px`;
		document.body.style.left = "0";
		document.body.style.right = "0";
		document.body.style.width = "100%";
		document.body.style.overflow = "hidden";
	};

	const unlockPageScroll = () => {
		document.body.style.position = "";
		document.body.style.top = "";
		document.body.style.left = "";
		document.body.style.right = "";
		document.body.style.width = "";
		document.body.style.overflow = "";
		window.scrollTo(0, scrollLockY);
	};

	const openOverlay = (el) => {
		if (!el || el.dataset.overlayOpen === "1") return;
		el.dataset.overlayOpen = "1";
		el.classList.add("is-open");
		el.setAttribute("aria-hidden", "false");
		overlayOpenCount += 1;
		if (overlayOpenCount === 1) lockPageScroll();
	};

	const closeOverlay = (el) => {
		if (!el || el.dataset.overlayOpen !== "1") return;
		el.dataset.overlayOpen = "0";
		el.classList.remove("is-open");
		el.setAttribute("aria-hidden", "true");
		overlayOpenCount = Math.max(0, overlayOpenCount - 1);
		if (overlayOpenCount === 0) unlockPageScroll();
	};

	const closeEndgameModal = () => {
		if (!endgameEl) return;
		closeOverlay(endgameEl);
	};

	const openEndgameModal = (score0, score1) => {
		if (!endgameEl || !endgameResultEl || !endgameScoreEl || !endgameReasonEl) return;
		const p0 = basePlayerName(player0Name, "Player 1");
		const p1 = basePlayerName(player1Name, "Player 2");
		let resultText = "Draw";
		let winnerClass = "";
		if (winner === 0 || (winner === null && score0 > score1)) {
			resultText = `${p0} wins`;
			winnerClass = "is-p0";
		} else if (winner === 1 || (winner === null && score1 > score0)) {
			resultText = `${p1} wins`;
			winnerClass = "is-p1";
		}

		endgameResultEl.textContent = resultText;
		endgameResultEl.classList.remove("is-p0", "is-p1");
		if (winnerClass) endgameResultEl.classList.add(winnerClass);
		endgameScoreEl.innerHTML = `Final score: <span class="popugame__score--p0">${score0}</span> : <span class="popugame__score--p1">${score1}</span>`;
		if (endedReason === "concede") {
			let concederName = "";
			let concederClass = "";
			if (winner === 0) {
				concederName = p1;
				concederClass = "popugame__name--p1";
			} else if (winner === 1) {
				concederName = p0;
				concederClass = "popugame__name--p0";
			}
			if (concederName) {
				endgameReasonEl.innerHTML = `Reason: <span class="${concederClass}">${escapeHtml(concederName)}</span> conceded`;
			} else {
				endgameReasonEl.textContent = "Reason: concede";
			}
		} else {
			endgameReasonEl.textContent = `Reason: ${humanEndReason(endedReason)}`;
		}

		const delta0 = formatDelta(eloDeltaP0);
		const delta1 = formatDelta(eloDeltaP1);
		const nextElo0 = Number.isFinite(Number(player0Elo)) ? Math.trunc(Number(player0Elo)) : null;
		const nextElo1 = Number.isFinite(Number(player1Elo)) ? Math.trunc(Number(player1Elo)) : null;
		const showElo = Boolean(
			isMultiplayer &&
			ratingsApplied &&
			delta0 !== null &&
			delta1 !== null &&
			nextElo0 !== null &&
			nextElo1 !== null
		);
		if (endgameEloEl && endgameEloP0El && endgameEloP1El) {
			endgameEloEl.hidden = !showElo;
			if (showElo) {
				const delta0Class = delta0.startsWith("+") ? "popugame__delta--positive" : "popugame__delta--negative";
				const delta1Class = delta1.startsWith("+") ? "popugame__delta--positive" : "popugame__delta--negative";
				endgameEloP0El.innerHTML = `<span class="popugame__name--p0">${escapeHtml(p0)}</span> ELO: <span class="popugame__elo-value">${nextElo0}</span> (<span class="${delta0Class}">${delta0}</span>)`;
				endgameEloP1El.innerHTML = `<span class="popugame__name--p1">${escapeHtml(p1)}</span> ELO: <span class="popugame__elo-value">${nextElo1}</span> (<span class="${delta1Class}">${delta1}</span>)`;
			}
		}

		if (endgamePlayAgainEl) endgamePlayAgainEl.hidden = isMultiplayer;
		if (endgameHostEl) endgameHostEl.hidden = !isMultiplayer;
		if (endgameJoinEl) endgameJoinEl.hidden = !isMultiplayer;

		openOverlay(endgameEl);
	};

	let dialogResolve = null;
	const showDialog = ({ title, bodyHtml, confirmText = "OK", cancelText = "Cancel", hideCancel = false }) => {
		if (!dialogEl || !dialogTitleEl || !dialogBodyEl || !dialogConfirmEl || !dialogCancelEl) {
			return Promise.resolve(false);
		}
		dialogTitleEl.textContent = title || "Notice";
		dialogBodyEl.innerHTML = bodyHtml || "";
		dialogConfirmEl.textContent = confirmText;
		dialogCancelEl.textContent = cancelText;
		dialogCancelEl.style.display = hideCancel ? "none" : "inline-flex";
		openOverlay(dialogEl);
		return new Promise((resolve) => { dialogResolve = resolve; });
	};
	const hideDialog = (result) => {
		if (!dialogEl) return;
		closeOverlay(dialogEl);
		if (dialogResolve) dialogResolve(result);
		dialogResolve = null;
	};

	const createAnonymousGuestToken = () => {
		const rnd = Math.random().toString(36).slice(2, 14);
		return `anon:${rnd}`;
	};

	const getStoredGuestName = () => {
		const localValue = (window.localStorage.getItem(GUEST_KEY) || "").trim();
		if (localValue) return localValue;
		const pendingValue = (window.sessionStorage.getItem(PENDING_GUEST_KEY) || "").trim();
		if (pendingValue) {
			window.localStorage.setItem(GUEST_KEY, pendingValue);
			return pendingValue;
		}
		const generated = createAnonymousGuestToken();
		window.localStorage.setItem(GUEST_KEY, generated);
		window.sessionStorage.setItem(PENDING_GUEST_KEY, generated);
		return generated;
	};

	const makeGrid = (value = 0) => (
		Array.from({ length: size }, () => Array.from({ length: size }, () => value))
	);

	const cloneGrid = (sourceGrid) => (
		Array.isArray(sourceGrid)
			? sourceGrid.map((row) => (Array.isArray(row) ? row.slice() : []))
			: makeGrid(0)
	);

	const snapshotState = () => ({
		grid: cloneGrid(grid),
		turn,
		player,
		gameOver,
	});

	const stateSignature = (snapshot) => (
		JSON.stringify({
			size,
			turnLimit,
			grid: snapshot.grid,
			turn: snapshot.turn,
			player: snapshot.player,
			gameOver: snapshot.gameOver,
		})
	);

	const restoreSnapshot = (snapshot) => {
		grid = cloneGrid(snapshot.grid);
		turn = Number.isFinite(snapshot.turn) ? snapshot.turn : 0;
		player = snapshot.player === 1 ? 1 : 0;
		gameOver = Boolean(snapshot.gameOver);
		legalMoves = [makeGrid(true), makeGrid(true)];
		updateLegalMoves();
	};

	const outOfBounds = (row, col) => (
		row < 0 || row >= size || col < 0 || col >= size
	);

	const initBoard = () => {
		boardEl.style.setProperty("--grid-size", size);
		boardEl.innerHTML = "";
		if (turnTrackEl) {
			turnTrackEl.style.setProperty("--turn-limit", turnLimit);
			turnTrackEl.innerHTML = "";
			for (let i = 0; i < turnLimit; i += 1) {
				const t = turnLimit <= 1 ? 0 : i / (turnLimit - 1);
				const hue = 140 + (210 - 140) * t;
				const sat = 58 + (68 - 58) * t;
				const light = 45 + (56 - 45) * t;
				const block = document.createElement("span");
				block.className = "popugame__turnbar-block";
				block.style.setProperty("--block-color", `hsl(${hue} ${sat}% ${light}%)`);
				turnTrackEl.appendChild(block);
			}
		}
		for (let r = 0; r < size; r += 1) {
			for (let c = 0; c < size; c += 1) {
				const btn = document.createElement("button");
				btn.type = "button";
				btn.className = "popugame__cell";
				btn.dataset.row = String(r);
				btn.dataset.col = String(c);
				btn.addEventListener("click", onCellClick);
				boardEl.appendChild(btn);
			}
		}
	};

	const clearLocalStorageState = () => {
		try {
			window.localStorage.removeItem(STORAGE_KEY);
			window.localStorage.removeItem(HISTORY_KEY);
		} catch (_) {
			// ignore storage errors (private mode / quota)
		}
	};

	const clearHistoryStorage = () => {
		try {
			window.localStorage.removeItem(HISTORY_KEY);
		} catch (_) {
			// ignore storage errors (private mode / quota)
		}
	};

	const saveHistory = () => {
		if (isMultiplayer) return;
		if (!moveHistory.length) {
			clearHistoryStorage();
			return;
		}
		const payload = {
			version: 1,
			size,
			turnLimit,
			baseState: stateSignature(snapshotState()),
			history: moveHistory,
		};
		try {
			window.localStorage.setItem(HISTORY_KEY, JSON.stringify(payload));
		} catch (_) {
			// ignore storage errors (private mode / quota)
		}
	};

	const loadHistory = () => {
		if (isMultiplayer) return false;
		try {
			const raw = window.localStorage.getItem(HISTORY_KEY);
			if (!raw) return false;
			const data = JSON.parse(raw);
			if (!data || data.version !== 1 || data.size !== size || data.turnLimit !== turnLimit) {
				clearHistoryStorage();
				return false;
			}
			if (data.baseState !== stateSignature(snapshotState())) {
				clearHistoryStorage();
				return false;
			}
			if (!Array.isArray(data.history)) {
				clearHistoryStorage();
				return false;
			}
			moveHistory = data.history
				.filter((entry) => entry && Array.isArray(entry.grid))
				.map((entry) => ({
					grid: cloneGrid(entry.grid),
					turn: Number.isFinite(entry.turn) ? entry.turn : 0,
					player: entry.player === 1 ? 1 : 0,
					gameOver: Boolean(entry.gameOver),
				}));
			return true;
		} catch (_) {
			clearHistoryStorage();
			return false;
		}
	};

	const resetGame = () => {
		if (isMultiplayer) return;
		clearLocalStorageState();
		moveHistory = [];
		grid = makeGrid(0);
		turn = 0;
		player = 0;
		gameOver = false;
		shownEndgameToken = null;
		closeEndgameModal();
		legalMoves = [makeGrid(true), makeGrid(true)];
		saveState();
		updateUI();
	};

	const onCellClick = (event) => {
		if (gameOver) return;
		const btn = event.currentTarget;
		const row = Number.parseInt(btn.dataset.row || "0", 10);
		const col = Number.parseInt(btn.dataset.col || "0", 10);
		if (!legalMoves[player][row][col]) return;
		stepAndAdvance(row, col);
	};

	const stepAndAdvance = (row, col) => {
		if (isMultiplayer) {
			sendMove(row, col);
			return;
		}
		moveHistory.push(snapshotState());
		stepGame(row, col);
		player ^= 1;
		if (turn >= turnLimit) {
			gameOver = true;
		}
		saveState();
		updateUI();
	};

	const undoMove = () => {
		if (isMultiplayer) return;
		if (!moveHistory.length) return;
		const previous = moveHistory.pop();
		restoreSnapshot(previous);
		saveState();
		updateUI();
	};

	const stepGame = (row, col) => {
		turn += 1;
		checkClaim(player, row, col);
		updateLegalMoves();
	};

	const updateLegalMoves = () => {
		const occupiedMask = p0Token | p1Token;
		for (let r = 0; r < size; r += 1) {
			for (let c = 0; c < size; c += 1) {
				const cell = grid[r][c];
				const occupied = (cell & occupiedMask) !== 0;
				const c0 = (cell & p0Claim) !== 0;
				const c1 = (cell & p1Claim) !== 0;
				legalMoves[0][r][c] = !occupied && !c1;
				legalMoves[1][r][c] = !occupied && !c0;
			}
		}
	};

	const checkLine = (mask, start, end, step) => {
		let maxContinuous = 0;
		let continuous = 0;
		let maxStart = null;
		let maxEnd = null;
		let currStart = null;
		let lastMask = false;
		let row = start[0];
		let col = start[1];
		const endRow = end[0] + step[0];
		const endCol = end[1] + step[1];

		while (row !== endRow || col !== endCol) {
			if (outOfBounds(row, col)) break;
			const cell = (grid[row][col] & mask) !== 0;
			if (cell) {
				if (lastMask) {
					continuous += 1;
				} else {
					currStart = [row, col];
					continuous = 1;
				}
				lastMask = true;
			} else {
				if (continuous > maxContinuous) {
					maxContinuous = continuous;
					maxStart = currStart;
					maxEnd = [row - step[0], col - step[1]];
				}
				continuous = 0;
				lastMask = false;
			}
			row += step[0];
			col += step[1];
		}

		if (continuous > maxContinuous) {
			maxContinuous = continuous;
			maxStart = currStart;
			maxEnd = [row - step[0], col - step[1]];
		}

		return { start: maxStart, end: maxEnd, continuous: maxContinuous };
	};

	const modifyClaims = (currentPlayer, markForClaim, start, step) => {
		if (!start) return;
		let curr = start;
		let op = 1;
		while (true) {
			if (outOfBounds(curr[0], curr[1]) || (grid[curr[0]][curr[1]] & gridValues[1 - currentPlayer].token)) {
				if (op > 0) {
					op = -1;
					curr = start;
					continue;
				}
				break;
			}
			markForClaim[curr[0]][curr[1]] = true;
			curr = [curr[0] + op * step[0], curr[1] + op * step[1]];
		}
	};

	const applyRemovalsAndClaims = (currentPlayer, markForRemove, markForClaim) => {
		for (let r = 0; r < size; r += 1) {
			for (let c = 0; c < size; c += 1) {
				if (markForRemove[r][c]) {
					grid[r][c] = noClaim;
				}
			}
		}

		const opponentClaim = gridValues[1 - currentPlayer].claim;
		for (let r = 0; r < size; r += 1) {
			for (let c = 0; c < size; c += 1) {
				if (!markForClaim[r][c]) continue;
				grid[r][c] &= (0b1111 - opponentClaim);
				grid[r][c] |= gridValues[currentPlayer].claim;
			}
		}
	};

	const checkClaim = (currentPlayer, row, col) => {
		const token = gridValues[currentPlayer].token;
		grid[row][col] |= token;

		const markForClaim = makeGrid(false);
		const markForRemove = makeGrid(false);

		let start = [row, Math.max(0, col - 2)];
		let end = [row, Math.min(size - 1, col + 2)];
		let step = [0, 1];
		let cont = checkLine(token, start, end, step);
		if (cont.continuous >= 3 && cont.start && cont.end) {
			for (let c = cont.start[1]; c <= cont.end[1]; c += 1) {
				markForRemove[row][c] = true;
			}
			modifyClaims(currentPlayer, markForClaim, cont.start, step);
		}

		start = [Math.max(0, row - 2), col];
		end = [Math.min(size - 1, row + 2), col];
		step = [1, 0];
		cont = checkLine(token, start, end, step);
		if (cont.continuous >= 3 && cont.start && cont.end) {
			for (let r = cont.start[0]; r <= cont.end[0]; r += 1) {
				markForRemove[r][col] = true;
			}
			modifyClaims(currentPlayer, markForClaim, cont.start, step);
		}

		const diagCandidates1 = [
			[row - 2, col - 2],
			[row - 1, col - 1],
			[row, col],
			[row + 1, col + 1],
			[row + 2, col + 2],
		].filter(([r, c]) => !outOfBounds(r, c));
		start = diagCandidates1[0];
		end = diagCandidates1[diagCandidates1.length - 1];
		step = [1, 1];
		if (start && end) {
			cont = checkLine(token, start, end, step);
			if (cont.continuous >= 3 && cont.start && cont.end) {
				for (let r = cont.start[0]; r <= cont.end[0]; r += 1) {
					const c = r - cont.start[0] + cont.start[1];
					markForRemove[r][c] = true;
				}
				modifyClaims(currentPlayer, markForClaim, cont.start, step);
			}
		}

		const diagCandidates2 = [
			[row - 2, col + 2],
			[row - 1, col + 1],
			[row, col],
			[row + 1, col - 1],
			[row + 2, col - 2],
		].filter(([r, c]) => !outOfBounds(r, c));
		start = diagCandidates2[0];
		end = diagCandidates2[diagCandidates2.length - 1];
		step = [1, -1];
		if (start && end) {
			cont = checkLine(token, start, end, step);
			if (cont.continuous >= 3 && cont.start && cont.end) {
				for (let r = cont.start[0]; r <= cont.end[0]; r += 1) {
					const c = cont.start[1] - (r - cont.start[0]);
					markForRemove[r][c] = true;
				}
				modifyClaims(currentPlayer, markForClaim, cont.start, step);
			}
		}

		applyRemovalsAndClaims(currentPlayer, markForRemove, markForClaim);
	};

	const calculateScores = () => {
		let p0 = 0;
		let p1 = 0;
		for (let r = 0; r < size; r += 1) {
			for (let c = 0; c < size; c += 1) {
				const cell = grid[r][c];
				if (cell & p0Claim) p0 += 1;
				if (cell & p1Claim) p1 += 1;
			}
		}
		return [p0, p1];
	};

	const updateUI = () => {
		root.dataset.activePlayer = String(player);
		const [score0, score1] = calculateScores();
		score0El.textContent = String(score0);
		score1El.textContent = String(score1);
		turnEl.textContent = String(Math.max(0, turnLimit - turn));
		if (turnTrackEl) {
			const remaining = Math.max(0, turnLimit - turn);
			const blocks = turnTrackEl.querySelectorAll(".popugame__turnbar-block");
			blocks.forEach((block, idx) => {
				block.classList.toggle("is-remaining", idx < remaining);
			});
		}

		if (gameStatus === "waiting") {
			statusEl.textContent = "Waiting for opponent…";
		} else if (gameOver) {
			const p0 = basePlayerName(player0Name, "Player 1");
			const p1 = basePlayerName(player1Name, "Player 2");
			let winnerText = "It's a draw!";
			if (score0 > score1) winnerText = `${p0} wins!`;
			if (score1 > score0) winnerText = `${p1} wins!`;
			if (winner === 0) winnerText = `${p0} wins!`;
			if (winner === 1) winnerText = `${p1} wins!`;
			statusEl.textContent = `Game over: ${winnerText}`;
		} else {
			const p0 = basePlayerName(player0Name, "Player 1");
			const p1 = basePlayerName(player1Name, "Player 2");
			statusEl.textContent = player === 0 ? `${p0} (X) to move` : `${p1} (O) to move`;
		}

		const cells = boardEl.querySelectorAll(".popugame__cell");
		cells.forEach((btn) => {
			const r = Number.parseInt(btn.dataset.row || "0", 10);
			const c = Number.parseInt(btn.dataset.col || "0", 10);
			const cell = grid[r][c];
			btn.textContent = "";
			btn.classList.remove("is-claim-p0", "is-claim-p1", "is-token-p0", "is-token-p1");
			if (cell & p0Claim) btn.classList.add("is-claim-p0");
			if (cell & p1Claim) btn.classList.add("is-claim-p1");
			if (cell & p0Token) {
				btn.classList.add("is-token-p0");
				btn.textContent = "X";
			}
			if (cell & p1Token) {
				btn.classList.add("is-token-p1");
				btn.textContent = "O";
			}
			const isTurn = playerIndex === null || playerIndex === player;
			btn.disabled = gameOver || gameStatus === "waiting" || !isTurn || !legalMoves[player][r][c];
		});

		if (concedeBtn) {
			const finished = gameOver || gameStatus === "finished";
			concedeBtn.hidden = finished;
			concedeBtn.disabled = finished;
			concedeBtn.classList.toggle("is-hidden", finished);
		}
		if (isMultiplayer && postgameButtons && postgameButtons.length > 0) {
			postgameButtons.forEach((btn) => {
				btn.hidden = !gameOver;
			});
		}
		if (undoBtn) {
			undoBtn.disabled = isMultiplayer || moveHistory.length === 0;
		}

		if (gameOver) {
			const token = isMultiplayer
				? `v${stateVersion}`
				: `t${turn}-w${winner === null ? "d" : winner}-s${score0}-${score1}`;
			if (shownEndgameToken !== token) {
				shownEndgameToken = token;
				openEndgameModal(score0, score1);
			}
		}
	};

	const saveState = () => {
		if (isMultiplayer) return;
		const payload = {
			version: 1,
			size,
			turnLimit,
			grid,
			turn,
			player,
			gameOver,
		};
		try {
			window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
		} catch (_) {
			// ignore storage errors (private mode / quota)
		}
		saveHistory();
	};

	const loadState = () => {
		if (isMultiplayer) return false;
		try {
			const raw = window.localStorage.getItem(STORAGE_KEY);
			if (!raw) return false;
			const data = JSON.parse(raw);
			if (!data || data.version !== 1) {
				clearLocalStorageState();
				return false;
			}
			if (data.size !== size || data.turnLimit !== turnLimit) {
				clearLocalStorageState();
				return false;
			}
			if (!Array.isArray(data.grid) || data.grid.length !== size) {
				clearLocalStorageState();
				return false;
			}
			grid = cloneGrid(data.grid);
			turn = Number.isFinite(data.turn) ? data.turn : 0;
			player = data.player === 1 ? 1 : 0;
			gameOver = Boolean(data.gameOver);
			legalMoves = [makeGrid(true), makeGrid(true)];
			updateLegalMoves();
			if (!loadHistory()) {
				moveHistory = [];
			}
			return true;
		} catch (_) {
			clearLocalStorageState();
			return false;
		}
	};

	const apiPost = async (url, payload) => {
		const resp = await fetch(url, {
			method: "POST",
			headers: {
				"accept": "application/json",
				"content-type": "application/json",
			},
			body: JSON.stringify(payload || {}),
		});
		const json = await resp.json().catch(() => null);
		return { resp, json };
	};

	const maybeNotifyTurn = (nextActivePlayer, nextStatus, nextTurn) => {
		if (!isMultiplayer) return;
		if (playerIndex === null) return;
		if (nextStatus !== "active") return;
		if (previousActivePlayer === null) return;
		if (previousActivePlayer === playerIndex) return;
		if (nextActivePlayer !== playerIndex) return;
		if (windowFocused && document.visibilityState === "visible") return;
		if (!("Notification" in window)) return;
		if (Notification.permission !== "granted") return;

		const body = `Your turn in game ${gameCode} (turn ${nextTurn}/${turnLimit}).`;
		new Notification("PopuGame", {
			body,
			tag: `popugame-turn-${gameCode}`,
		});
	};

	const primeNotificationPermission = () => {
		if (!isMultiplayer) return;
		if (!("Notification" in window)) return;
		if (Notification.permission !== "default") return;
		Notification.requestPermission().catch(() => {});
	};

	const setStateFromServer = (state) => {
		if (!state) return;
		const nextActivePlayer = state.active_player === 1 ? 1 : 0;
		const nextTurn = Number.isFinite(state.turn) ? state.turn : 0;
		const nextStatus = state.status || "active";

		grid = state.grid || makeGrid(0);
		turn = nextTurn;
		player = nextActivePlayer;
		gameStatus = nextStatus;
		winner = state.winner ?? null;
		endedReason = state.ended_reason || null;
		ratingsApplied = Boolean(state.ratings_applied);
		eloDeltaP0 = state.elo_delta_p0;
		eloDeltaP1 = state.elo_delta_p1;
		player0Elo = state.elo_after_p0 ?? state.player0_elo;
		player1Elo = state.elo_after_p1 ?? state.player1_elo;
		stateVersion = Number.isFinite(state.state_version) ? state.state_version : stateVersion;
		gameOver = gameStatus === "finished";
		if (!gameOver) {
			shownEndgameToken = null;
			closeEndgameModal();
		}
		if (sharePanelEl) {
			const bothPlayersConnected = Boolean((state.player0_name || "").trim() && (state.player1_name || "").trim());
			const opponentConnected = playerIndex === 0
				? Boolean((state.player1_name || "").trim())
				: playerIndex === 1
					? Boolean((state.player0_name || "").trim())
					: false;
			sharePanelEl.hidden = bothPlayersConnected || opponentConnected || gameStatus !== "waiting";
			if (!sharePanelEl.hidden) updateShareFields();
		}
		if (name0El) {
			player0Name = basePlayerName(state.player0_name, "Player 1");
			name0El.textContent = formatPlayerName(state.player0_name, state.player0_elo, "Player 1");
		}
		if (name1El) {
			player1Name = basePlayerName(state.player1_name, "Player 2");
			name1El.textContent = formatPlayerName(state.player1_name, state.player1_elo, "Player 2");
		}
		legalMoves = [makeGrid(true), makeGrid(true)];
		updateLegalMoves();
		updateUI();
		maybeNotifyTurn(nextActivePlayer, nextStatus, nextTurn);
		previousActivePlayer = nextActivePlayer;
	};

	const joinGame = async () => {
		const payload = { code: gameCode, guest_name: getStoredGuestName() };
		const { json } = await apiPost("/api/popugame/join", payload);
		if (json && json.invalid_link) {
			window.location.href = json.redirect_url || "/popugame/invalid";
			return;
		}
		if (!json || !json.ok) {
			await showDialog({
				title: "Join Failed",
				bodyHtml: `<p>${(json && json.message) || "Failed to join game."}</p>`,
				confirmText: "OK",
				hideCancel: true,
			});
			return;
		}
		playerIndex = json.player;
		setStateFromServer(json.state);
		startStream();
	};

	const startStream = () => {
		if (!gameCode) return;
		if (eventSource) eventSource.close();
		const url = `/api/popugame/stream/${encodeURIComponent(gameCode)}?since=${stateVersion}`;
		eventSource = new EventSource(url);
		eventSource.addEventListener("state", (event) => {
			const payload = JSON.parse(event.data || "{}");
			if (payload && payload.state) {
				setStateFromServer(payload.state);
			}
		});
		eventSource.addEventListener("error", () => {
			setTimeout(() => startStream(), 2000);
		});
	};

	const sendMove = async (row, col) => {
		if (!gameCode) return;
		if (playerIndex === null) return;
		if (playerIndex !== player) return;
		const payload = { code: gameCode, row, col, guest_name: getStoredGuestName() };
		const { json } = await apiPost("/api/popugame/move", payload);
		if (!json || !json.ok) {
			await showDialog({
				title: "Move Failed",
				bodyHtml: `<p>${(json && json.message) || "Move failed."}</p>`,
				confirmText: "OK",
				hideCancel: true,
			});
			return;
		}
		setStateFromServer(json.state);
	};

if (resetBtn) {
	resetBtn.addEventListener("click", resetGame);
}
if (undoBtn) {
	undoBtn.addEventListener("click", undoMove);
}
if (rulesBtn && modalEl) {
	rulesBtn.addEventListener("click", () => {
		openOverlay(modalEl);
	});
}
if (closeBtn && modalEl) {
	closeBtn.addEventListener("click", () => {
		closeOverlay(modalEl);
	});
}
if (modalEl) {
	modalEl.addEventListener("click", (event) => {
		if (event.target !== modalEl) return;
		closeOverlay(modalEl);
	});
}
if (dialogCloseEl) {
	dialogCloseEl.addEventListener("click", () => hideDialog(false));
}
if (dialogCancelEl) {
	dialogCancelEl.addEventListener("click", () => hideDialog(false));
}
if (dialogConfirmEl) {
	dialogConfirmEl.addEventListener("click", () => hideDialog(true));
}
if (dialogEl) {
	dialogEl.addEventListener("click", (event) => {
		if (event.target !== dialogEl) return;
		hideDialog(false);
	});
}
	root.addEventListener("click", async (event) => {
	const btn = event.target.closest("[data-popugame-copy-btn]");
	if (!btn) return;
	const chip = btn.closest("[data-popugame-copy-chip]");
	if (!chip) return;
	const value = chip.dataset.copyText || "";
	if (!value) return;
	const tooltip = chip.querySelector("[data-popugame-tooltip]");
	if (typeof window.copyTextWithToast === "function") {
		await window.copyTextWithToast(value, chip, tooltip);
		return;
	}
	try {
		await navigator.clipboard.writeText(value);
	} catch (_) {
		await showDialog({
			title: "Copy failed",
			bodyHtml: "<p>Copy to clipboard failed.</p>",
			confirmText: "OK",
			hideCancel: true,
		});
	}
});
	const handleHostGame = async () => {
		const guestName = getStoredGuestName();
		const { json } = await apiPost("/api/popugame/create", { guest_name: guestName });
		if (!json || !json.ok) {
			await showDialog({
				title: "Host failed",
				bodyHtml: `<p>${(json && json.message) || "Failed to create game."}</p>`,
				confirmText: "OK",
				hideCancel: true,
			});
			return;
		}
		window.sessionStorage.setItem(PENDING_GUEST_KEY, guestName);
		window.sessionStorage.setItem("popugameHosted", json.code);
		window.location.href = `/popugame/${json.code}`;
	};

	const handleJoinByCode = async () => {
		const ok = await showDialog({
			title: "Join Game",
			bodyHtml: `
				<p>Enter a 6-character game code.</p>
				<div class="popugame__dialog-field">
					<label>Game code</label>
					<input type="text" data-popugame-dialog-input maxlength="6" placeholder="ABC123">
				</div>
			`,
			confirmText: "Join",
			cancelText: "Cancel",
		});
		if (!ok) return;
		const input = dialogBodyEl ? dialogBodyEl.querySelector("[data-popugame-dialog-input]") : null;
		const cleaned = input ? input.value.trim().toUpperCase() : "";
		if (cleaned.length !== 6 || !/^[A-Z0-9]+$/.test(cleaned)) {
			await showDialog({
				title: "Invalid code",
				bodyHtml: "<p>Codes are 6 letters/numbers.</p>",
				confirmText: "OK",
				hideCancel: true,
			});
			return;
		}
		window.location.href = `/popugame/${cleaned}`;
	};

if (hostBtn) {
	hostBtn.addEventListener("click", handleHostGame);
}
if (joinBtn) {
	joinBtn.addEventListener("click", handleJoinByCode);
}
if (concedeBtn) {
	concedeBtn.addEventListener("click", async () => {
		if (!gameCode) return;
		const ok = await showDialog({
			title: "Concede Game?",
			bodyHtml: "<p>This will end the game and award the win to your opponent.</p>",
			confirmText: "Concede",
			cancelText: "Cancel",
		});
		if (!ok) return;
		const payload = { code: gameCode };
		payload.guest_name = getStoredGuestName();
		const { json } = await apiPost("/api/popugame/concede", payload);
		if (!json || !json.ok) {
			await showDialog({
				title: "Concede failed",
				bodyHtml: `<p>${(json && json.message) || "Concede failed."}</p>`,
				confirmText: "OK",
				hideCancel: true,
			});
			return;
		}
		setStateFromServer(json.state);
	});
}
if (endgameCloseEl) {
	endgameCloseEl.addEventListener("click", closeEndgameModal);
}
if (endgameDismissEl) {
	endgameDismissEl.addEventListener("click", closeEndgameModal);
}
if (endgamePlayAgainEl) {
	endgamePlayAgainEl.addEventListener("click", () => {
		closeEndgameModal();
		resetGame();
	});
}
if (endgameHostEl) {
	endgameHostEl.addEventListener("click", async () => {
		closeEndgameModal();
		await handleHostGame();
	});
}
if (endgameJoinEl) {
	endgameJoinEl.addEventListener("click", async () => {
		closeEndgameModal();
		await handleJoinByCode();
	});
}
if (endgameEl) {
	endgameEl.addEventListener("click", (event) => {
		if (event.target !== endgameEl) return;
		closeEndgameModal();
	});
}

window.addEventListener("focus", () => {
	windowFocused = true;
});
window.addEventListener("blur", () => {
	windowFocused = false;
});
document.addEventListener("visibilitychange", () => {
	windowFocused = document.visibilityState === "visible";
});
document.addEventListener("keydown", (event) => {
	if (event.key !== "Escape") return;
	closeEndgameModal();
});
root.addEventListener("click", primeNotificationPermission, { once: true });

initBoard();
if (isMultiplayer) {
	joinGame();
	const hosted = window.sessionStorage.getItem("popugameHosted");
	if (hosted && hosted === gameCode) {
		window.sessionStorage.removeItem("popugameHosted");
	}
} else if (!loadState()) {
	resetGame();
} else {
	updateUI();
}
})();
