(() => {
	const root = document.querySelector("[data-pg-replay]");
	if (!root) return;

	const code = (root.dataset.pgReplayCode || "").trim().toUpperCase();
	const boardEl = root.querySelector("[data-pg-replay-board]");
	const titleEl = root.querySelector("[data-pg-replay-title]");
	const metaEl = root.querySelector("[data-pg-replay-meta]");
	const stepEl = root.querySelector("[data-pg-replay-step]");
	const noticeEl = root.querySelector("[data-pg-replay-notice]");
	const scoreboxEl = root.querySelector("[data-pg-replay-scorebox]");
	const scoreP0El = root.querySelector("[data-pg-replay-score-p0]");
	const scoreP1El = root.querySelector("[data-pg-replay-score-p1]");
	const nameP0El = root.querySelector("[data-pg-replay-name-p0]");
	const nameP1El = root.querySelector("[data-pg-replay-name-p1]");
	const firstBtn = document.getElementById("pg-replay-first");
	const prevBtn = document.getElementById("pg-replay-prev");
	const nextBtn = document.getElementById("pg-replay-next");
	const lastBtn = document.getElementById("pg-replay-last");
	const autoplayBtn = document.getElementById("pg-replay-autoplay");

	// Bitmask constants matching popugame.js
	const p0Token = 0b0001;
	const p0Claim = 0b0010;
	const p1Token = 0b0100;
	const p1Claim = 0b1000;

	let moves = [];
	let currentStep = 0; // 0 = initial empty board, 1..N = after move N
	let gridSize = 9;
	let autoplayTimer = null;
	let state = null;

	const escapeHtml = (v) => String(v)
		.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;").replaceAll('"', "&quot;");

	const totalSteps = () => moves.length;

	const initBoard = (size) => {
		gridSize = size;
		boardEl.style.setProperty("--grid-size", size);
		boardEl.innerHTML = "";
		for (let r = 0; r < size; r++) {
			for (let c = 0; c < size; c++) {
				const btn = document.createElement("button");
				btn.type = "button";
				btn.className = "popugame__cell";
				btn.dataset.row = String(r);
				btn.dataset.col = String(c);
				btn.disabled = true;
				boardEl.appendChild(btn);
			}
		}
	};

	const renderGrid = (grid) => {
		const cells = boardEl.querySelectorAll(".popugame__cell");
		cells.forEach((btn) => {
			const r = Number.parseInt(btn.dataset.row || "0", 10);
			const c = Number.parseInt(btn.dataset.col || "0", 10);
			const cell = (grid && grid[r] && grid[r][c]) || 0;
			btn.textContent = "";
			btn.classList.remove("is-claim-p0", "is-claim-p1", "is-token-p0", "is-token-p1");
			if (cell & p0Claim) btn.classList.add("is-claim-p0");
			if (cell & p1Claim) btn.classList.add("is-claim-p1");
			if (cell & p0Token) { btn.classList.add("is-token-p0"); btn.textContent = "X"; }
			if (cell & p1Token) { btn.classList.add("is-token-p1"); btn.textContent = "O"; }
		});
	};

	const makeEmptyGrid = (size) =>
		Array.from({ length: size }, () => Array(size).fill(0));

	const goToStep = (step) => {
		currentStep = Math.max(0, Math.min(step, totalSteps()));
		let grid;
		if (currentStep === 0) {
			grid = makeEmptyGrid(gridSize);
		} else {
			const move = moves[currentStep - 1];
			grid = move.grid_state || makeEmptyGrid(gridSize);
		}
		renderGrid(grid);
		updateScore(grid);
		if (stepEl) stepEl.textContent = `${currentStep} / ${totalSteps()}`;
		if (firstBtn) firstBtn.disabled = currentStep === 0;
		if (prevBtn) prevBtn.disabled = currentStep === 0;
		if (nextBtn) nextBtn.disabled = currentStep >= totalSteps();
		if (lastBtn) lastBtn.disabled = currentStep >= totalSteps();

		// Highlight the cell that was just played
		if (currentStep > 0) {
			const m = moves[currentStep - 1];
			const cell = boardEl.querySelector(`[data-row="${m.row_idx}"][data-col="${m.col_idx}"]`);
			if (cell) {
				cell.classList.add("popugame-replay__last-move");
				setTimeout(() => cell.classList.remove("popugame-replay__last-move"), 600);
			}
		}
	};

	const stopAutoplay = () => {
		clearInterval(autoplayTimer);
		autoplayTimer = null;
		if (autoplayBtn) { autoplayBtn.textContent = "▶ Play"; autoplayBtn.classList.remove("btn--accent"); }
	};

	const startAutoplay = () => {
		if (currentStep >= totalSteps()) goToStep(0);
		autoplayTimer = setInterval(() => {
			if (currentStep >= totalSteps()) {
				stopAutoplay();
				return;
			}
			goToStep(currentStep + 1);
		}, 700);
		if (autoplayBtn) { autoplayBtn.textContent = "⏸ Pause"; autoplayBtn.classList.add("btn--accent"); }
	};

	const toggleAutoplay = () => {
		if (autoplayTimer) stopAutoplay();
		else startAutoplay();
	};

	const formatResult = (gameState) => {
		const p0 = escapeHtml(gameState.player0_name || "Player 1");
		const p1 = escapeHtml(gameState.player1_name || "Player 2");
		if (gameState.winner === 0) return `${p0} wins`;
		if (gameState.winner === 1) return `${p1} wins`;
		return "Draw";
	};

	const computeScore = (grid) => {
		let p0 = 0, p1 = 0;
		for (const row of (grid || [])) {
			for (const cell of (row || [])) {
				if (cell & p0Claim) p0++;
				if (cell & p1Claim) p1++;
			}
		}
		return { p0, p1 };
	};

	const updateScore = (grid) => {
		if (!scoreboxEl) return;
		const { p0, p1 } = computeScore(grid);
		if (scoreP0El) scoreP0El.textContent = String(p0);
		if (scoreP1El) scoreP1El.textContent = String(p1);
	};

	// ── Load data ─────────────────────────────────────────

	const load = async () => {
		if (titleEl) titleEl.textContent = "Loading replay…";
		let data = null;
		try {
			const resp = await fetch(`/api/popugame/replay/${encodeURIComponent(code)}`, {
				headers: { "accept": "application/json" },
			});
			data = await resp.json().catch(() => null);
		} catch (_) {}

		if (!data || !data.ok) {
			if (titleEl) titleEl.textContent = "Replay unavailable";
			if (noticeEl) { noticeEl.hidden = false; noticeEl.textContent = "This game could not be loaded."; }
			return;
		}

		state = data.state;
		moves = data.moves || [];
		const size = (state && state.grid_size) || 9;

		const p0 = escapeHtml(state.player0_name || "Player 1");
		const p1 = escapeHtml(state.player1_name || "Player 2");
		if (nameP0El) nameP0El.textContent = state.player0_name || "Player 1";
		if (nameP1El) nameP1El.textContent = state.player1_name || "Player 2";
		if (scoreboxEl) scoreboxEl.hidden = false;
		if (titleEl) titleEl.textContent = `${state.player0_name || "Player 1"} vs ${state.player1_name || "Player 2"}`;
		if (metaEl) {
			const result = formatResult(state);
			const reason = state.ended_reason === "concede" ? "by concession"
				: state.ended_reason === "abandon" ? "by abandonment"
				: state.ended_reason === "turn_limit" ? "at turn limit"
				: "";
			metaEl.textContent = `${result}${reason ? " " + reason : ""} · Game ${code} · ${state.turn || 0} turns`;
		}

		initBoard(size);

		if (data.no_recording || moves.length === 0) {
			// No move history — show final board state and inform user
			const finalGrid = state.grid || makeEmptyGrid(size);
			renderGrid(finalGrid);
			updateScore(finalGrid);
			if (stepEl) stepEl.textContent = "No recording";
			if (firstBtn) firstBtn.disabled = true;
			if (prevBtn) prevBtn.disabled = true;
			if (nextBtn) nextBtn.disabled = true;
			if (lastBtn) lastBtn.disabled = true;
			if (autoplayBtn) autoplayBtn.disabled = true;
			if (noticeEl) {
				noticeEl.hidden = false;
				noticeEl.textContent = "Move-by-move recording is not available for this game (played before recording was enabled). Showing final board state.";
			}
			return;
		}

		goToStep(0);
	};

	// ── Event listeners ───────────────────────────────────

	if (firstBtn) firstBtn.addEventListener("click", () => { stopAutoplay(); goToStep(0); });
	if (prevBtn) prevBtn.addEventListener("click", () => { stopAutoplay(); goToStep(currentStep - 1); });
	if (nextBtn) nextBtn.addEventListener("click", () => { stopAutoplay(); goToStep(currentStep + 1); });
	if (lastBtn) lastBtn.addEventListener("click", () => { stopAutoplay(); goToStep(totalSteps()); });
	if (autoplayBtn) autoplayBtn.addEventListener("click", toggleAutoplay);

	document.addEventListener("keydown", (e) => {
		if (e.key === "ArrowLeft") { stopAutoplay(); goToStep(currentStep - 1); }
		if (e.key === "ArrowRight") { stopAutoplay(); goToStep(currentStep + 1); }
		if (e.key === " ") { e.preventDefault(); toggleAutoplay(); }
	});

	load();
})();
