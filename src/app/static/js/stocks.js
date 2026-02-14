(() => {
	const root = document.querySelector("[data-stock-viewer]");
	if (!root) return;

	const searchInput = document.getElementById("stock-search-input");
	const searchStatus = document.getElementById("stock-search-status");
	const resultsEl = document.getElementById("stock-search-results");
	const symbolEl = document.getElementById("stock-symbol-label");
	const nameEl = document.getElementById("stock-name-label");
	const priceEl = document.getElementById("stock-price-label");
	const viewStatus = document.getElementById("stock-view-status");
	const plotEl = document.getElementById("stock-plot");
	const rangeButtons = Array.from(document.querySelectorAll(".stock-range-btn"));
	const defaultTimeframe = (root.dataset.defaultTimeframe || "5Min").trim() || "5Min";
	const rollingWindowDays = Number.parseInt(root.dataset.rollingWindowDays || "7", 10);

	let activeSymbol = "";
	let activeName = "";
	let activeRange = "1W";
	let searchTimer = null;
	let currentSeries = [];

	const escapeHtml = (value) => String(value || "")
		.replaceAll("&", "&amp;")
		.replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;")
		.replaceAll('"', "&quot;")
		.replaceAll("'", "&#39;");

	const getPlotTheme = () => {
		const s = getComputedStyle(document.documentElement);
		return {
			bg: (s.getPropertyValue("--contents") || "").trim() || "#fff",
			grid: (s.getPropertyValue("--border") || "").trim() || "#ddd",
			font: (s.getPropertyValue("--default") || "").trim() || "#222",
			line: (s.getPropertyValue("--dark_blue") || "").trim() || "#569cd6",
			hover: (s.getPropertyValue("--form_body") || "").trim() || "#fff",
		};
	};

	const formatTickLabel = (date, range) => {
		if (!(date instanceof Date) || !Number.isFinite(date.getTime())) return "";
		if (range === "1D") {
			return new Intl.DateTimeFormat("en-GB", {
				hour: "2-digit",
				minute: "2-digit",
				hour12: false,
				timeZone: "America/New_York",
			}).format(date);
		}
		if (range === "1W") {
			return new Intl.DateTimeFormat("en-GB", {
				day: "numeric",
				month: "short",
				timeZone: "America/New_York",
			}).format(date);
		}
		return new Intl.DateTimeFormat("en-GB", {
			day: "numeric",
			month: "short",
			hour: "2-digit",
			minute: "2-digit",
			hour12: false,
			timeZone: "America/New_York",
		}).format(date);
	};

	const buildIntradayTicks = (rawTs, range) => {
		if (!Array.isArray(rawTs) || rawTs.length === 0) {
			return { vals: [], texts: [] };
		}
		const targetTicks = 9;
		const step = Math.max(1, Math.floor((rawTs.length - 1) / (targetTicks - 1)));
		const vals = [];
		const texts = [];
		for (let i = 0; i < rawTs.length; i += step) {
			vals.push(i);
			texts.push(formatTickLabel(rawTs[i], range));
		}
		const last = rawTs.length - 1;
		if (vals[vals.length - 1] !== last) {
			vals.push(last);
			texts.push(formatTickLabel(rawTs[last], range));
		}
		return { vals, texts };
	};

	const setRangeActive = (range) => {
		activeRange = range;
		rangeButtons.forEach((btn) => {
			btn.classList.toggle("is-active", btn.dataset.range === range);
		});
	};

	const renderPlotly = (points) => {
		currentSeries = Array.isArray(points) ? points : [];
		if (!plotEl || typeof Plotly === "undefined") return;

		const xs = [];
		const rawTs = [];
		const ys = [];
		const isIntraday = /min|hour/i.test(defaultTimeframe);
		for (const p of currentSeries) {
			if (!p || p.ts == null || p.close == null) continue;
			const x = new Date(p.ts);
			const y = Number(p.close);
			if (!Number.isFinite(x.getTime()) || !Number.isFinite(y)) continue;
			rawTs.push(x);
			xs.push(isIntraday ? (rawTs.length - 1) : x);
			ys.push(y);
		}

		const theme = getPlotTheme();
		const intradayTicks = isIntraday ? buildIntradayTicks(rawTs, activeRange) : { vals: [], texts: [] };
		const trace = {
			x: xs,
			y: ys,
			mode: "lines",
			line: { width: 2.5, color: theme.line },
			customdata: rawTs,
			hovertemplate: "%{customdata|%Y-%m-%d %H:%M}<br>Close: %{y:.2f}<extra></extra>",
			connectgaps: false,
		};

		const layout = {
			title: xs.length ? `${activeSymbol} Close (${defaultTimeframe})` : "No data",
			paper_bgcolor: "rgba(0,0,0,0)",
			plot_bgcolor: "rgba(0,0,0,0)",
			font: { color: theme.font },
			xaxis: {
				type: isIntraday ? "linear" : "date",
				showgrid: true,
				gridcolor: theme.grid,
				tickmode: isIntraday ? "array" : "auto",
				tickvals: isIntraday ? intradayTicks.vals : undefined,
				ticktext: isIntraday ? intradayTicks.texts : undefined,
				tickformat: isIntraday ? undefined : "%m-%d %H:%M",
				nticks: 10,
				showspikes: true,
				spikemode: "across",
				spikesnap: "cursor",
				spikecolor: theme.grid,
				spikethickness: 1,
			},
			yaxis: {
				showgrid: true,
				gridcolor: theme.grid,
				fixedrange: true,
			},
			margin: { t: 46, r: 16, b: 42, l: 56 },
			hovermode: isIntraday ? "x" : "x unified",
			hoverlabel: { bgcolor: theme.hover },
		};

		const config = {
			responsive: true,
			displaylogo: false,
			modeBarButtonsToRemove: ["zoom2d", "select2d", "lasso2d", "autoScale2d"],
		};

		Plotly.react(plotEl, [trace], layout, config);
	};

	const loadPrices = async () => {
		if (!activeSymbol) return;
		viewStatus.textContent = `Loading ${activeSymbol} prices...`;
		const params = new URLSearchParams({
			range: activeRange,
			timeframe: defaultTimeframe,
			limit: "5000",
		});
		try {
			const resp = await fetch(`/api/stocks/${encodeURIComponent(activeSymbol)}/prices?${params.toString()}`);
			const data = await resp.json();
			if (!resp.ok || !data.ok) {
				throw new Error(data.message || "Failed to load prices.");
			}
			renderPlotly(data.points || []);
			const latest = data.summary && Number.isFinite(Number(data.summary.latest_close))
				? Number(data.summary.latest_close)
				: null;
			const pct = data.summary && Number.isFinite(Number(data.summary.change_pct))
				? Number(data.summary.change_pct)
				: null;
			const latestText = latest === null ? "-" : `$${latest.toFixed(2)}`;
			const pctText = pct === null ? "" : ` (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)`;
			const pctClass = pct === null ? "is-neutral" : (pct >= 0 ? "is-positive" : "is-negative");
			symbolEl.textContent = activeSymbol;
			nameEl.textContent = activeName;
			priceEl.innerHTML = `${latestText}<span class="stocks-page__pct ${pctClass}">${pctText}</span>`;
			const retentionText = Number.isFinite(rollingWindowDays) && rollingWindowDays > 0
				? ` | rolling ${rollingWindowDays}D`
				: "";
			viewStatus.textContent = `Loaded ${data.count} bars | ${defaultTimeframe} | ${activeRange}${retentionText}`;
		} catch (err) {
			renderPlotly([]);
			viewStatus.textContent = err instanceof Error ? err.message : "Unable to load prices.";
		}
	};

	const selectSymbol = (symbol, name) => {
		activeSymbol = symbol;
		activeName = name || "";
		loadPrices();
	};

	const renderResults = (results, message) => {
		if (!results.length) {
			resultsEl.innerHTML = "";
			searchStatus.textContent = message || "No matching symbols found.";
			return;
		}
		resultsEl.innerHTML = results.map((r) => `
			<li>
				<button type="button" data-symbol="${escapeHtml(r.symbol)}" data-name="${escapeHtml(r.name || "")}">
					<span><strong>${escapeHtml(r.symbol)}</strong> ${escapeHtml(r.name || "")}</span>
					<span>${escapeHtml(r.exchange || "")}</span>
				</button>
			</li>
		`).join("");
		searchStatus.textContent = `${results.length} result(s)`;
	};

	const performSearch = async () => {
		const q = (searchInput.value || "").trim();
		if (!q) {
			resultsEl.innerHTML = "";
			searchStatus.textContent = "Type to search...";
			return;
		}
		searchStatus.textContent = "Searching...";
		try {
			const params = new URLSearchParams({ q, limit: "25" });
			const resp = await fetch(`/api/stocks/search?${params.toString()}`);
			const data = await resp.json();
			if (!resp.ok || !data.ok) throw new Error(data.message || "Search failed.");
			renderResults(data.results || [], data.message || "");
		} catch (err) {
			resultsEl.innerHTML = "";
			searchStatus.textContent = err instanceof Error ? err.message : "Search failed.";
		}
	};

	searchInput.addEventListener("input", () => {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(performSearch, 250);
	});

	resultsEl.addEventListener("click", (ev) => {
		const btn = ev.target && ev.target.closest && ev.target.closest("button[data-symbol]");
		if (!btn) return;
		const symbol = btn.getAttribute("data-symbol") || "";
		const name = btn.getAttribute("data-name") || "";
		if (!symbol) return;
		selectSymbol(symbol, name);
	});

	rangeButtons.forEach((btn) => {
		btn.addEventListener("click", () => {
			const next = btn.dataset.range || "1M";
			setRangeActive(next);
			loadPrices();
		});
	});

	// Re-theme chart when light/dark mode changes.
	const mo = new MutationObserver(() => renderPlotly(currentSeries));
	mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

	renderPlotly([]);
})();
