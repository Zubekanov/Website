(function () {
	const card = document.querySelector("[data-mc-status]");
	if (!card) return;

	const hostEl = card.querySelector("[data-mc-host]");
	const hostChip = card.querySelector("[data-mc-host-chip]");
	const hostCopy = card.querySelector("[data-mc-copy]");
	const hostTooltip = card.querySelector("[data-mc-tooltip]");
	const motdEl = card.querySelector("[data-mc-motd]");
	const playersEl = card.querySelector("[data-mc-players]");
	const versionEl = card.querySelector("[data-mc-version]");
	const latencyEl = card.querySelector("[data-mc-latency]");
	const pillEl = card.querySelector("[data-mc-status-pill]");
	const noteEl = card.querySelector("[data-mc-status-note]");
	const whitelistBanner = document.querySelector("[data-mc-whitelist]");
	const whitelistToggle = document.querySelector("[data-mc-toggle]");
	const registrationWrap = document.getElementById("minecraft-registration-wrap");

	const STORAGE_KEY = "minecraftStatusCache";
	let fetchedAt = null;
	let refreshing = false;
	let refreshTimer = null;

	const setPill = (state, text) => {
		pillEl.textContent = text;
		pillEl.classList.remove(
			"minecraft-status-pill--online",
			"minecraft-status-pill--offline",
			"minecraft-status-pill--loading",
		);
		pillEl.classList.add(`minecraft-status-pill--${state}`);
	};

	const formatLocalTime = (dateObj) => {
		if (!dateObj) return "unknown time";
		const now = new Date();
		const sameDay = now.toDateString() === dateObj.toDateString();
		const options = sameDay
			? { hour: "numeric", minute: "2-digit" }
			: { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
		return new Intl.DateTimeFormat(undefined, options).format(dateObj);
	};

	const updateNote = () => {
		const timeText = formatLocalTime(fetchedAt);
		if (refreshing) {
			noteEl.textContent = `Refreshing… last checked ${timeText}.`;
		} else {
			noteEl.textContent = `Last checked ${timeText}.`;
		}
	};

	const setOffline = (message) => {
		setPill("offline", "Offline");
		motdEl.textContent = "No server detected.";
		playersEl.textContent = "--";
		versionEl.textContent = "--";
		latencyEl.textContent = "--";
		noteEl.textContent = message || "Server is offline or unreachable.";
	};

	const applyData = (data) => {
		if (!data || !data.ok) {
			setOffline(data && data.error ? data.error : "Status unavailable.");
			return;
		}

		refreshing = Boolean(data.refreshing);
		fetchedAt = data.fetched_at ? new Date(data.fetched_at) : null;

		if (!data.online) {
			setOffline(data.error || "Server is offline.");
			if (fetchedAt) {
				noteEl.textContent = `Last checked ${formatLocalTime(fetchedAt)}.`;
			}
		} else {
			setPill("online", "Online");
			if (hostEl && data.host) hostEl.textContent = data.host;
			motdEl.textContent = data.motd || "Server is online.";
			if (data.players_online != null && data.players_max != null) {
				playersEl.textContent = `${data.players_online} / ${data.players_max}`;
			} else if (data.players_online != null) {
				playersEl.textContent = String(data.players_online);
			} else {
				playersEl.textContent = "--";
			}
			versionEl.textContent = data.version || "--";
			latencyEl.textContent = data.latency_ms != null ? `${data.latency_ms} ms` : "--";
		}

		updateNote();

		if (refreshing) {
			if (!refreshTimer) {
				refreshTimer = setTimeout(fetchStatus, 5000);
			}
		}
	};

	const saveCache = (data) => {
		if (!data || !data.ok) return;
		const payload = {
			ok: data.ok,
			online: data.online,
			host: data.host,
			port: data.port,
			motd: data.motd,
			players_online: data.players_online,
			players_max: data.players_max,
			version: data.version,
			latency_ms: data.latency_ms,
			fetched_at: data.fetched_at,
		};
		try {
			window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
		} catch {
			// ignore storage failures
		}
	};

	const loadCache = () => {
		try {
			const raw = window.localStorage.getItem(STORAGE_KEY);
			if (!raw) return null;
			const parsed = JSON.parse(raw);
			if (!parsed || !parsed.ok) return null;
			return parsed;
		} catch {
			return null;
		}
	};

	const fetchStatus = () => {
		if (refreshTimer) {
			clearTimeout(refreshTimer);
			refreshTimer = null;
		}

		fetch("/api/minecraft/status")
			.then((res) => res.json())
			.then((data) => {
				applyData(data);
				saveCache(data);
			})
			.catch((err) => {
				setOffline(err ? String(err) : "Status unavailable.");
			});
	};

	const copyHost = () => {
		if (!hostEl) return;
		const text = (hostEl.textContent || "").trim();
		if (!text) return;
		const finish = () => {
			if (!hostChip) return;
			hostChip.classList.add("is-copied");
			if (hostTooltip) {
				hostTooltip.textContent = "Copied";
				hostTooltip.setAttribute("aria-hidden", "false");
			}
			setTimeout(() => hostChip.classList.remove("is-copied"), 1200);
			if (hostTooltip) {
				setTimeout(() => hostTooltip.setAttribute("aria-hidden", "true"), 1200);
			}
		};
		if (navigator.clipboard && navigator.clipboard.writeText) {
			navigator.clipboard.writeText(text).then(finish).catch(finish);
		} else {
			try {
				const area = document.createElement("textarea");
				area.value = text;
				area.setAttribute("readonly", "readonly");
				area.style.position = "absolute";
				area.style.left = "-9999px";
				document.body.appendChild(area);
				area.select();
				document.execCommand("copy");
				document.body.removeChild(area);
			} catch {
				// ignore
			}
			finish();
		}
	};

	if (hostCopy) {
		hostCopy.addEventListener("click", (e) => {
			e.preventDefault();
			copyHost();
		});
	}

	if (
		whitelistBanner
		&& whitelistToggle
		&& registrationWrap
		&& whitelistBanner.getAttribute("data-is-whitelisted") === "true"
	) {
		whitelistToggle.addEventListener("click", () => {
			registrationWrap.classList.toggle("is-hidden");
		});
	}

	const cached = loadCache();
	if (cached) {
		applyData({ ...cached, refreshing: true });
	}

	fetchStatus();
})();
