let METRIC_META = null;

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

async function fetchMetric(metric) {
	const url = `/api/metrics/${metric}?count=720`;
	const resp = await fetch(url);
	return await resp.json();
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

async function initMetricPlot(div, meta) {
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

	const hist = await fetchMetric(metric);
	if (hist.error) {
		div.innerHTML = `<p>Error: ${hist.error}</p>`;
		return null;
	}

	const x = (hist.timestamps || []).map(tsToDate);
	const y = hist.data || [];

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
			tickformat: "%H:%M",
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

	const lastTs = (hist.timestamps && hist.timestamps.length)
		? hist.timestamps[hist.timestamps.length - 1]
		: null;

	return {
		div,
		metric,
		lastTs
	};
}

// --- One poller to update all plots ----------------------------

function startUnifiedPoller(plotStates, periodMs = 1000) {
	let lastSeenTs = null;

	setInterval(async () => {
		const res = await fetchLatestRow();
		if (res.error || !res.data) return;

		const row = res.data;
		const ts = row.ts;

		if (ts == null) return;

		// Drop duplicates
		if (lastSeenTs != null && ts <= lastSeenTs) return;
		lastSeenTs = ts;

		const xVal = tsToDate(ts);

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

// --- Auto-init on page load -----------------------------------

document.addEventListener("DOMContentLoaded", () => {
	(async () => {
		const meta = await loadMetricMeta();

		const divs = Array.from(document.querySelectorAll(".metric-plot"));
		const states = [];

		// Initialise all plots (history)
		for (const div of divs) {
			const st = await initMetricPlot(div, meta);
			if (st) states.push(st);
		}

		// One request per second updates all plots
		startUnifiedPoller(states, 1000);
	})();
});
