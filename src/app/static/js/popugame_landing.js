(() => {
	const hostPrivateBtn = document.getElementById("pg-host-private");
	const hostPublicBtn = document.getElementById("pg-host-public");
	const joinInput = document.getElementById("pg-join-input");
	const joinBtn = document.getElementById("pg-join-btn");
	const lobbyRefreshBtn = document.getElementById("pg-lobby-refresh");
	const leaderboardListEl = document.querySelector("[data-pg-leaderboard-list]");
	const lobbyListEl = document.querySelector("[data-pg-lobby-list]");
	const historyListEl = document.querySelector("[data-pg-history-list]");

	const LOBBY_REFRESH_INTERVAL_MS = 15_000;

	const bootData = (() => {
		try { return JSON.parse(document.getElementById("boot-data")?.textContent || "{}"); }
		catch { return {}; }
	})();
	const isLoggedIn = bootData.is_logged_in === true;

	const escapeHtml = (v) => String(v)
		.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;").replaceAll('"', "&quot;");

	const apiPost = async (url, payload) => {
		const resp = await fetch(url, {
			method: "POST",
			headers: { "accept": "application/json", "content-type": "application/json" },
			body: JSON.stringify(payload || {}),
		});
		const json = await resp.json().catch(() => null);
		return { resp, json };
	};

	const apiGet = async (url) => {
		const resp = await fetch(url, { headers: { "accept": "application/json" } });
		const json = await resp.json().catch(() => null);
		return { resp, json };
	};

	const timeAgo = (isoStr) => {
		if (!isoStr) return "";
		const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
		if (diff < 60) return `${diff}s ago`;
		if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
		return `${Math.floor(diff / 3600)}h ago`;
	};

	const shortDate = (isoStr) => {
		if (!isoStr) return "";
		const d = new Date(isoStr);
		return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
	};

	// ── Leaderboard ──────────────────────────────────────

	const renderLeaderboard = (entries) => {
		if (!leaderboardListEl) return;
		if (!entries || entries.length === 0) {
			leaderboardListEl.innerHTML = '<div class="popugame-landing__loading">No rated games yet.</div>';
			return;
		}
		leaderboardListEl.innerHTML = entries.map((e, i) => `
			<div class="popugame-leaderboard-row">
				<span class="popugame-leaderboard-row__rank">${i + 1}</span>
				<span class="popugame-leaderboard-row__name">${escapeHtml(e.name)}</span>
				<span class="popugame-leaderboard-row__elo">${e.elo}</span>
				<span class="popugame-leaderboard-row__record">${e.wins}W ${e.losses}L ${e.draws}D</span>
			</div>
		`).join("");
	};

	const loadLeaderboard = async () => {
		const { json } = await apiGet("/api/popugame/leaderboard");
		renderLeaderboard(json && json.ok ? json.entries : []);
	};

	// ── Public Lobby ─────────────────────────────────────

	const renderLobby = (games) => {
		if (!lobbyListEl) return;
		if (!games || games.length === 0) {
			lobbyListEl.innerHTML = '<div class="popugame-lobby-empty">No public games waiting. Start one!</div>';
			return;
		}
		lobbyListEl.innerHTML = games.map((g) => {
			let badge = "";
			if (g.is_members_only) {
				badge = '<span class="pg-lobby-badge pg-lobby-badge--members">Members Elo Match</span>';
			} else if (g.is_casual) {
				badge = '<span class="pg-lobby-badge pg-lobby-badge--casual">Casual</span>';
			}

			const hasGuestToken = localStorage.getItem(`popugameGuestToken:${g.code.toLowerCase()}`) !== null;
			const isOwnGame = g.is_own_game || myGameCodes.has(g.code) || hasGuestToken;
			let joinAction;
			if (isOwnGame) {
				joinAction = `
					<div class="pg-lobby-own-actions">
						<a href="/popugame/${escapeHtml(g.code)}" class="btn btn--ghost pg-lobby-join" style="padding:0.2rem 0.6rem;font-size:0.8rem;">Rejoin</a>
						<button class="icon-btn icon-btn--delete" type="button" data-pg-delete-code="${escapeHtml(g.code)}" aria-label="Delete match">
							<img src="/static/img/bin.png" alt="Delete" class="icon-btn__img">
						</button>
					</div>`;
			} else if (g.is_members_only && !isLoggedIn) {
				joinAction = `<a href="/login" class="btn btn--ghost pg-lobby-join" style="padding:0.2rem 0.6rem;font-size:0.8rem;">Register to join</a>`;
			} else {
				joinAction = `<a href="/popugame/${escapeHtml(g.code)}" class="btn btn--ghost pg-lobby-join" style="padding:0.2rem 0.6rem;font-size:0.8rem;">Join</a>`;
			}

			return `
				<div class="popugame-lobby-row">
					<span class="popugame-lobby-row__host">${escapeHtml(g.host_name || "Anonymous")}</span>
					${badge}
					<span class="popugame-lobby-row__age">${timeAgo(g.created_at)}</span>
					${joinAction}
				</div>
			`;
		}).join("");
	};

	const loadLobby = async () => {
		const { json } = await apiGet("/api/popugame/public");
		renderLobby(json && json.ok ? json.games : []);
	};

	// ── History ──────────────────────────────────────────

	const renderHistory = (games) => {
		if (!historyListEl) return;
		if (!games || games.length === 0) {
			historyListEl.innerHTML = '<div class="popugame-history-empty">No completed games yet.</div>';
			return;
		}
		historyListEl.innerHTML = games.map((g) => {
			const p0 = escapeHtml(g.p0_name || "Player 1");
			const p1 = escapeHtml(g.p1_name || "Player 2");
			let resultText = "Draw";
			let resultClass = "popugame-history-row__result--draw";
			if (g.winner === 0) { resultText = `${p0} wins`; resultClass = "popugame-history-row__result--p0"; }
			if (g.winner === 1) { resultText = `${p1} wins`; resultClass = "popugame-history-row__result--p1"; }
			const meta = `Turn ${g.turn || 0} · ${shortDate(g.finished_at)}`;
			const code = escapeHtml(g.code || "");
			return `
				<div class="popugame-history-row">
					<span class="popugame-history-row__players">${p0} vs ${p1}</span>
					<span class="popugame-history-row__result ${resultClass}">${resultText}</span>
					<a href="/popugame/replay/${code}" class="btn btn--ghost popugame-history-row__watch" style="padding: 0.2rem 0.6rem; font-size: 0.8rem;">Watch</a>
					<span class="popugame-history-row__meta">${escapeHtml(meta)}</span>
				</div>
			`;
		}).join("");
	};

	const loadHistory = async () => {
		const { json } = await apiGet("/api/popugame/history?limit=20");
		renderHistory(json && json.ok ? json.games : []);
	};

	// ── Host settings modal ──────────────────────────────

	let settingsOverlay = null;

	const buildSettingsModal = () => {
		const el = document.createElement("div");
		el.className = "pg-host-overlay";
		el.id = "pg-host-overlay";
		el.hidden = true;
		el.innerHTML = `
			<div class="pg-host-modal" role="dialog" aria-modal="true" aria-labelledby="pg-host-modal-title">
				<div class="pg-host-modal__title" id="pg-host-modal-title">Start Public Game</div>
				<div class="pg-host-modal__options">
					<label class="pg-host-option">
						<input class="pg-host-option__radio" type="radio" name="pg-game-mode" value="standard" checked>
						<div class="pg-host-option__body">
							<span class="pg-host-option__label">Standard</span>
							<span class="pg-host-option__desc">Open to all. ELO tracked for registered accounts.</span>
						</div>
					</label>
					<label class="pg-host-option">
						<input class="pg-host-option__radio" type="radio" name="pg-game-mode" value="casual">
						<div class="pg-host-option__body">
							<span class="pg-host-option__label">Casual</span>
							<span class="pg-host-option__desc">Open to all. ELO not saved for either player.</span>
						</div>
					</label>
					<label class="pg-host-option">
						<input class="pg-host-option__radio" type="radio" name="pg-game-mode" value="members">
						<div class="pg-host-option__body">
							<span class="pg-host-option__label">Members Only</span>
							<span class="pg-host-option__desc">Registered accounts only. ELO always recorded.</span>
						</div>
					</label>
				</div>
				<div class="pg-host-modal__actions">
					<button class="btn btn--ghost" id="pg-host-cancel" type="button">Cancel</button>
					<button class="btn btn--accent" id="pg-host-confirm" type="button">Create Game</button>
				</div>
			</div>
		`;
		document.body.appendChild(el);
		el.addEventListener("click", (e) => { if (e.target === el) closeSettingsModal(); });
		el.querySelector("#pg-host-cancel").addEventListener("click", closeSettingsModal);
		el.querySelector("#pg-host-confirm").addEventListener("click", confirmAndHost);
		document.addEventListener("keydown", (e) => {
			if (e.key === "Escape" && settingsOverlay && !settingsOverlay.hidden) closeSettingsModal();
		});
		return el;
	};

	const openSettingsModal = () => {
		if (!settingsOverlay) settingsOverlay = buildSettingsModal();
		settingsOverlay.querySelector('[value="standard"]').checked = true;
		settingsOverlay.hidden = false;
		settingsOverlay.querySelector("#pg-host-confirm").focus();
	};

	const closeSettingsModal = () => {
		if (settingsOverlay) settingsOverlay.hidden = true;
		hostPublicBtn?.focus();
	};

	const confirmAndHost = async () => {
		const mode = settingsOverlay?.querySelector('[name="pg-game-mode"]:checked')?.value || "standard";
		closeSettingsModal();
		await hostGame(true, {
			is_casual: mode === "casual",
			is_members_only: mode === "members",
		});
	};

	// ── Actions ──────────────────────────────────────────

	const myGameCodes = (() => {
		try { return new Set(JSON.parse(localStorage.getItem("pg_my_codes") || "[]")); }
		catch { return new Set(); }
	})();

	const rememberMyCode = (code) => {
		myGameCodes.add(code);
		try { localStorage.setItem("pg_my_codes", JSON.stringify([...myGameCodes])); } catch {}
	};

	const hostGame = async (isPublic, settings = {}) => {
		const btn = isPublic ? hostPublicBtn : hostPrivateBtn;
		if (btn) { btn.disabled = true; btn.textContent = "Creating…"; }
		const payload = { is_public: isPublic, ...settings };
		const { json } = await apiPost("/api/popugame/create", payload);
		if (!json || !json.ok) {
			if (btn) {
				btn.disabled = false;
				btn.textContent = isPublic ? "Start Public Game" : "Host Private Game";
			}
			alert((json && json.message) || "Failed to create game.");
			return;
		}
		rememberMyCode(json.code);
		window.location.href = `/popugame/${json.code}`;
	};

	const joinByCode = () => {
		if (!joinInput) return;
		const cleaned = joinInput.value.trim().toUpperCase();
		if (cleaned.length !== 6 || !/^[A-Z0-9]+$/.test(cleaned)) {
			joinInput.focus();
			joinInput.select();
			return;
		}
		window.location.href = `/popugame/${cleaned}`;
	};

	// ── Event listeners ──────────────────────────────────

	if (hostPrivateBtn) hostPrivateBtn.addEventListener("click", () => hostGame(false));
	if (hostPublicBtn) hostPublicBtn.addEventListener("click", openSettingsModal);
	if (joinBtn) joinBtn.addEventListener("click", joinByCode);
	if (joinInput) {
		joinInput.addEventListener("keydown", (e) => { if (e.key === "Enter") joinByCode(); });
		joinInput.addEventListener("input", () => {
			joinInput.value = joinInput.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
		});
	}
	if (lobbyRefreshBtn) lobbyRefreshBtn.addEventListener("click", loadLobby);

	if (lobbyListEl) {
		lobbyListEl.addEventListener("click", async (e) => {
			const btn = e.target.closest("[data-pg-delete-code]");
			if (!btn) return;
			const code = btn.dataset.pgDeleteCode;
			if (!code) return;
			if (!confirm("Delete this game?")) return;
			btn.disabled = true;
			const resp = await fetch("/api/popugame/abandon", {
				method: "POST",
				headers: { "content-type": "application/json", "accept": "application/json" },
				body: JSON.stringify({ code }),
			});
			const json = await resp.json().catch(() => null);
			if (json && json.ok) {
				loadLobby();
			} else {
				btn.disabled = false;
				alert((json && json.message) || "Could not delete the game.");
			}
		});
	}

	// ── Auto-refresh lobby ───────────────────────────────

	let lobbyRefreshTimer = null;
	const scheduleLobbyRefresh = () => {
		clearTimeout(lobbyRefreshTimer);
		lobbyRefreshTimer = setTimeout(() => {
			if (document.visibilityState === "visible") loadLobby();
			scheduleLobbyRefresh();
		}, LOBBY_REFRESH_INTERVAL_MS);
	};
	document.addEventListener("visibilitychange", () => {
		if (document.visibilityState === "visible") loadLobby();
	});

	// ── Initial load ─────────────────────────────────────

	Promise.all([loadLeaderboard(), loadLobby(), loadHistory()]).then(() => {
		scheduleLobbyRefresh();
	});
})();
