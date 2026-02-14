let METRIC_META = null;
const HIST_CACHE = new Map();
const HIST_INFLIGHT = new Map();

async function loadMetricMeta() {
	if (METRIC_META) return METRIC_META;

	const resp = await fetch("/api/metrics/names");
	const json = await resp.json();

	METRIC_META = {
		names: json.names || {},
		units: json.units || {}
	};
	return METRIC_META;
}

// --- Helpers --------------------------------------------------

function tsToDate(ts) {
	return new Date(ts);
}

async function fetchMetric(metric, range) {
	let url = `/api/metrics/${metric}`;
	if (range && range.window && range.bucket) {
		url += `?window=${range.window}&bucket=${range.bucket}`;
	} else if (range && range.count) {
		url += `?count=${range.count}`;
	} else {
		url += `?count=720`;
	}
	const resp = await fetch(url);
	return await resp.json();
}

async function fetchMetricsBulk(metrics, range) {
	if (!metrics || !metrics.length) return null;
	let url = `/api/metrics/bulk?metrics=${encodeURIComponent(metrics.join(","))}`;
	if (range && range.window && range.bucket) {
		url += `&window=${range.window}&bucket=${range.bucket}`;
	} else if (range && range.count) {
		url += `&count=${range.count}`;
	} else {
		url += `&count=720`;
	}
	const resp = await fetch(url);
	return await resp.json();
}

function rangeKey(range) {
	if (!range) return "count=720";
	if (range.window && range.bucket) return `window=${range.window}&bucket=${range.bucket}`;
	if (range.count) return `count=${range.count}`;
	return "count=720";
}

async function getBulkCached(metrics, range) {
	const key = rangeKey(range);
	if (HIST_CACHE.has(key)) return HIST_CACHE.get(key);
	if (HIST_INFLIGHT.has(key)) return HIST_INFLIGHT.get(key);
	const promise = fetchMetricsBulk(metrics, range)
		.then((data) => {
			if (!data || data.error) return data;
			HIST_CACHE.set(key, data);
			return data;
		})
		.catch((err) => ({ error: String(err), timestamps: [], data: {} }))
		.finally(() => {
			HIST_INFLIGHT.delete(key);
		});
	HIST_INFLIGHT.set(key, promise);
	return promise;
}

function invalidateRangeCache(range) {
	const key = rangeKey(range);
	HIST_CACHE.delete(key);
	HIST_INFLIGHT.delete(key);
}

function getHistForMetric(bulk, metric) {
	if (!bulk || bulk.error) return null;
	if (!bulk.data || !Object.prototype.hasOwnProperty.call(bulk.data, metric)) return null;
	return {
		timestamps: bulk.timestamps || [],
		data: bulk.data[metric] || []
	};
}

async function fetchLatestRow() {
	const resp = await fetch("/api/metrics/update");
	return await resp.json();
}

function enableVerticalScrollOnPlot(div) {
	const selectors = [".nsewdrag", ".xy", ".x", ".y"];
	selectors.forEach(sel => {
		div.querySelectorAll(sel).forEach(el => {
			el.style.touchAction = "pan-y";
		});
	});
}

// --- Plot initialisation --------------------------------------

function getTickFormat(range) {
	if (!range) return "%H:%M";
	if (range.window && range.window >= 31536000) return "%b %Y";
	if (range.window && range.window >= 604800) return "%b %d";
	if (range.window && range.window >= 86400) return "%b %d %H:%M";
	return "%H:%M";
}

async function initMetricPlot(div, meta, range, bulk) {
	const metric = div.dataset.metric;
	const names = meta.names || {};
	const units = meta.units || {};

	const displayName = names[metric] || metric;
	const unit = units[metric] || "";

	const includeZero = (div.dataset.includeZero || "true") === "true";
	const fixedY = (div.dataset.fixedY || "true") === "true";

	const styles = getComputedStyle(div);
	const lineColor  = (styles.getPropertyValue("--plot-line")     || "").trim() || "#0af";
	const gridColor  = (styles.getPropertyValue("--plot-grid")     || "").trim() || "rgba(200,200,200,0.2)";
	const fontColor  = (styles.getPropertyValue("--plot-font")     || "").trim() || "currentColor";
	const hoverColor = (styles.getPropertyValue("--plot-hover-bg") || "").trim() || "rgba(0,0,0,0.7)";

	div.classList.add("is-loading");
	let hist = getHistForMetric(bulk, metric);
	if (!hist) {
		hist = await fetchMetric(metric, range);
	}
	if (hist.error) {
		div.innerHTML = `<p>Error: ${hist.error}</p>`;
		div.classList.remove("is-loading");
		return null;
	}

	let x = (hist.timestamps || []).map(tsToDate);
	let y = hist.data || [];
	if (range && range.window && range.bucket) {
		({ x, y } = buildBucketedSeries(hist, range));
	}

	let decimals = 1;
	if (unit === "B/s") decimals = 0;

	const hoverTemplate =
		"%{x|%H:%M:%S}<br>" +
		displayName + ": %{y:." + decimals + "f}" +
		(unit ? " " + unit : "") +
		"<extra></extra>";

	const trace = {
		x,
		y,
		mode: "lines",
		line: { width: 2, color: lineColor },
		connectgaps: false,
		hovertemplate: hoverTemplate,
		simplify: true
	};

	const layout = {
		title: displayName,
		dragmode: false,
		plot_bgcolor: "rgba(0,0,0,0)",
		paper_bgcolor: "rgba(0,0,0,0)",
		font: { color: fontColor },
		xaxis: {
			title: "Time",
			tickformat: getTickFormat(range),
			showgrid: true,
			gridcolor: gridColor,
		},
		yaxis: {
			title: `${displayName} (${unit})`,
			rangemode: includeZero ? "tozero" : undefined,
			fixedrange: fixedY,
		},
		margin: { t: 40, r: 20, b: 40, l: 60 },
		hovermode: "x unified",
		hoverlabel: { bgcolor: hoverColor }
	};

	const config = {
		responsive: true,
		displaylogo: false,
		modeBarButtonsToRemove: ["zoom2d", "select2d", "lasso2d"]
	};

	await Plotly.newPlot(div, [trace], layout, config);
	enableVerticalScrollOnPlot(div);
	requestAnimationFrame(() => {
		div.classList.remove("is-loading");
	});

	const lastTs = (hist.timestamps && hist.timestamps.length)
		? hist.timestamps[hist.timestamps.length - 1]
		: null;

	return {
		div,
		metric,
		lastTs
	};
}

function buildBucketedSeries(hist, range) {
	const bucketMs = range.bucket * 1000;
	const now = Date.now();
	const start = now - range.window * 1000;
	const data = new Map();

	const tsList = (hist.timestamps || []).map(tsToDate);
	const vals = hist.data || [];
	for (let i = 0; i < tsList.length; i++) {
		const t = tsList[i].getTime();
		const bucket = Math.floor(t / bucketMs) * bucketMs;
		data.set(bucket, vals[i]);
	}

	const x = [];
	const y = [];
	const keys = Array.from(data.keys()).sort((a, b) => a - b);
	if (keys.length === 0) return { x, y };

	const rangeStart = Math.max(start, keys[0]);
	const rangeEnd = Math.min(now, keys[keys.length - 1]);

	for (let t = rangeStart; t <= rangeEnd; t += bucketMs) {
		const key = Math.floor(t / bucketMs) * bucketMs;
		x.push(new Date(key));
		y.push(data.has(key) ? data.get(key) : null);
	}
	// Smooth historic data with a centered rolling average (7 buckets)
	const windowSize = 7;
	const half = Math.floor(windowSize / 2);
	const smoothed = y.map((_, idx) => {
		let sum = 0;
		let count = 0;
		for (let i = idx - half; i <= idx + half; i++) {
			if (i < 0 || i >= y.length) continue;
			const val = y[i];
			if (val == null || Number.isNaN(val)) continue;
			sum += val;
			count += 1;
		}
		return count ? sum / count : null;
	});
	return { x, y: smoothed };
}

// --- One poller to update all plots ----------------------------

function startUnifiedPoller(plotStates, periodMs = 1000) {
	let lastSeenTs = null;

	return setInterval(async () => {
		const res = await fetchLatestRow();
		if (res.error || !res.data) return;

		const row = res.data;
		const ts = row.ts;

		if (ts == null) return;

		// Drop duplicates
		if (lastSeenTs != null && ts <= lastSeenTs) return;
		lastSeenTs = ts;

		const xVal = tsToDate(ts);

		updateKpis(row);

		plotStates.forEach(st => {
			const v = row[st.metric];

			// If this metric wasn’t included / is null, skip
			if (v == null || Number.isNaN(v)) return;

			// If a plot is ahead of the unified ts, don’t backfill
			if (st.lastTs != null && ts <= st.lastTs) return;

			Plotly.extendTraces(st.div, {
				x: [[xVal]],
				y: [[v]]
			}, [0], 720);

			st.lastTs = ts;
		});
	}, periodMs);
}

function updateKpis(row) {
	if (!row) return;
	const cards = document.querySelectorAll("[data-metric-kpi]");
	cards.forEach((el) => {
		const metric = el.dataset.metricKpi;
		if (!metric) return;
		const v = row[metric];
		if (v == null || Number.isNaN(v)) return;
		el.textContent = typeof v === "number" ? v.toFixed(2) : String(v);
	});
}

// --- Auto-init on page load -----------------------------------

document.addEventListener("DOMContentLoaded", () => {
	(async () => {
		document.querySelectorAll(".metric-plot").forEach((el) => el.classList.add("is-loading"));
		const meta = await loadMetricMeta();
		const divs = Array.from(document.querySelectorAll(".metric-plot"));
		const states = [];
		let pollerId = null;

		let currentRange = { count: 720 };
		const controls = Array.from(document.querySelectorAll("[data-range]"));
		const liveBadge = document.querySelector("[data-live-badge]");

		function setActive(btn) {
			controls.forEach((b) => b.classList.toggle("is-active", b === btn));
		}

		async function updatePlotRange(st, range, bulk) {
			const metric = st.metric;
			const names = meta.names || {};
			const units = meta.units || {};
			const displayName = names[metric] || metric;
			const unit = units[metric] || "";

			const styles = getComputedStyle(st.div);
			const lineColor  = (styles.getPropertyValue("--plot-line")     || "").trim() || "#0af";
			const gridColor  = (styles.getPropertyValue("--plot-grid")     || "").trim() || "rgba(200,200,200,0.2)";
			const fontColor  = (styles.getPropertyValue("--plot-font")     || "").trim() || "currentColor";
			const hoverColor = (styles.getPropertyValue("--plot-hover-bg") || "").trim() || "rgba(0,0,0,0.7)";

			st.div.classList.add("is-loading");
			if (st.div.data) {
				Plotly.purge(st.div);
			}
			let hist = getHistForMetric(bulk, metric);
			if (!hist) {
				hist = await fetchMetric(metric, range);
			}
			if (hist.error) {
				st.div.classList.remove("is-loading");
				return;
			}

			let x = (hist.timestamps || []).map(tsToDate);
			let y = hist.data || [];
			if (range && range.window && range.bucket) {
				({ x, y } = buildBucketedSeries(hist, range));
			}

			let decimals = 1;
			if (unit === "B/s") decimals = 0;

			const hoverTemplate =
				"%{x|%Y-%m-%d %H:%M}<br>" +
				displayName + ": %{y:." + decimals + "f}" +
				(unit ? " " + unit : "") +
				"<extra></extra>";

			const trace = {
				x,
				y,
				mode: "lines",
				line: { width: 2, color: lineColor },
				connectgaps: false,
				hovertemplate: hoverTemplate,
				simplify: true
			};

			const layout = {
				title: displayName,
				dragmode: false,
				plot_bgcolor: "rgba(0,0,0,0)",
				paper_bgcolor: "rgba(0,0,0,0)",
				font: { color: fontColor },
				xaxis: {
					title: "Time",
					tickformat: getTickFormat(range),
					showgrid: true,
					gridcolor: gridColor,
				},
				yaxis: {
					title: `${displayName} (${unit})`,
					rangemode: "tozero",
					fixedrange: true,
				},
				margin: { t: 40, r: 20, b: 40, l: 60 },
				hovermode: "x unified",
				hoverlabel: { bgcolor: hoverColor }
			};

			const config = {
				responsive: true,
				displaylogo: false,
				modeBarButtonsToRemove: ["zoom2d", "select2d", "lasso2d"]
			};

			await Plotly.react(st.div, [trace], layout, config);
			st.div.classList.remove("is-loading");

			const lastTs = (hist.timestamps && hist.timestamps.length)
				? hist.timestamps[hist.timestamps.length - 1]
				: null;
			st.lastTs = lastTs;

		}

		async function applyRange(range, btn) {
			currentRange = range;
			if (btn) setActive(btn);

			const isLive = range && range.count === 720 && !range.window;
			if (liveBadge) liveBadge.textContent = isLive ? "LIVE" : "HISTORICAL";

			states.forEach((st) => st.div.classList.add("is-loading"));
			const metrics = states.map((st) => st.metric);
			let bulk;
			if (isLive) {
				// Always refresh live baseline when returning from historical views.
				invalidateRangeCache(range);
				bulk = await fetchMetricsBulk(metrics, range);
				if (bulk && !bulk.error) {
					HIST_CACHE.set(rangeKey(range), bulk);
				}
			} else {
				bulk = await getBulkCached(metrics, range);
			}
			await Promise.all(states.map((st) => updatePlotRange(st, range, bulk)));

			if (pollerId) {
				clearInterval(pollerId);
				pollerId = null;
			}
			if (isLive) {
				pollerId = startUnifiedPoller(states, 1000);
			}
		}

		for (const btn of controls) {
			btn.addEventListener("click", () => {
				const range = btn.dataset.range;
				if (btn.classList.contains("is-active")) return;
				if (range === "1h") applyRange({ count: 720 }, btn);
				if (range === "24h") applyRange({ window: 86400, bucket: 60 }, btn);
				if (range === "7d") applyRange({ window: 604800, bucket: 300 }, btn);
				if (range === "30d") applyRange({ window: 2592000, bucket: 60 }, btn);
				if (range === "1y") applyRange({ window: 31536000, bucket: 10800 }, btn);
			});
		}

		// Initialise default view
		const metrics = divs.map((div) => div.dataset.metric).filter(Boolean);
		const bulk = await getBulkCached(metrics, currentRange);
		for (const div of divs) {
			const st = await initMetricPlot(div, meta, currentRange, bulk);
			if (st) states.push(st);
		}
		const latest = await fetchLatestRow();
		if (latest && latest.data) updateKpis(latest.data);
		const kpiInterval = setInterval(async () => {
			const latestRow = await fetchLatestRow();
			if (latestRow && latestRow.data) updateKpis(latestRow.data);
		}, 1000);
		pollerId = startUnifiedPoller(states, 1000);
		if (controls.length) setActive(controls[0]);

		const historicRanges = [
			{ window: 86400, bucket: 60 },
			{ window: 604800, bucket: 300 },
			{ window: 2592000, bucket: 60 },
			{ window: 31536000, bucket: 10800 },
		];

		(async () => {
			for (const range of historicRanges) {
				try {
					await getBulkCached(metrics, range);
				} catch {
					// ignore prefetch errors
				}
			}
		})();

		function fitKpiLabels() {
			if (window.innerWidth > 720) return;
			const labels = document.querySelectorAll(".kpi-label");
			labels.forEach((label) => {
				const card = label.closest(".kpi-card");
				if (!card) return;
				const base = label.dataset.baseFont || getComputedStyle(label).fontSize;
				if (!label.dataset.baseFont) label.dataset.baseFont = base;
				const basePx = parseFloat(base);
				const maxWidth = card.clientWidth - 24; // padding + breathing room
				let low = 10;
				let high = basePx;
				let best = low;
				while (low <= high) {
					const mid = Math.floor((low + high) / 2 * 10) / 10;
					label.style.fontSize = `${mid}px`;
					if (label.scrollWidth <= maxWidth) {
						best = mid;
						low = mid + 0.1;
					} else {
						high = mid - 0.1;
					}
				}
				label.style.fontSize = `${best}px`;
			});
		}

		const fitLater = () => requestAnimationFrame(() => requestAnimationFrame(fitKpiLabels));
		fitLater();
		window.addEventListener("resize", fitLater);
	})();
});
