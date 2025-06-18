document.addEventListener('DOMContentLoaded', async () => {
	const METRICS    = [
		{ key: 'cpu_temp',    label: 'CPU Temp (°C)' },
		{ key: 'cpu_percent', label: 'CPU Utilisation (%)' },
		{ key: 'ram_used',    label: 'RAM Used (GiB)' },
		{ key: 'disk_used',   label: 'Disk Used (GiB)' },
	];
	const charts     = {};
	const INTERVAL   = 5000;       // 5 s
	const HOUR_MS    = 3600e3;     // 1 h
	const HALF_HOUR  = 30*60*1000; // 30 min

	// Fetch static caps
	let staticCaps = {};
	try {
		const res = await fetch('/api/static_metrics');
		if (!res.ok) throw res.status;
		staticCaps = await res.json();
	} catch (e) {
		console.error('static_metrics load failed', e);
	}

	// Instantiate charts
	METRICS.forEach(m => {
		const ctx = document.getElementById(m.key).getContext('2d');
		// Build y‐axis config
		const yScale = { beginAtZero: true };
		if (m.key === 'cpu_percent') {
			yScale.min = 0;
			yScale.max = 100;
		}
		if (m.key === 'ram_used' && staticCaps.ram_total) {
			yScale.min = 0;
			yScale.max = staticCaps.ram_total;
		}
		if (m.key === 'disk_used' && staticCaps.disk_total) {
			yScale.min = 0;
			yScale.max = staticCaps.disk_total;
		}
		if (m.key === 'cpu_temp') {
			yScale.min = 0;
			yScale.max = 80;
		}

		charts[m.key] = new Chart(ctx, {
			type: 'line',
			data: { datasets: [{
				label: m.label,
				data: [],
				parsing: false,
				pointRadius: 0,
				borderWidth: 1,
				spanGaps: false
			}]},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				layout: {
					padding: { bottom: 20 }
				},
				animation: false,
				interaction: { mode: 'index', intersect: false, axis: 'x' },
				plugins: {
					tooltip: {
						mode: 'index',
						intersect: false,
						callbacks: {
							title: items => new Date(items[0].parsed.x).toLocaleTimeString(),
							label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y}`
						}
					},
					zoom: { pan:{enabled:true,mode:'x'}, zoom:{wheel:{enabled:true},mode:'x'} }
				},
				scales: {
					x: {
						type: 'linear',
						ticks: {
							stepSize: HALF_HOUR,
							callback: v => {
								const d = new Date(v);
								return `${d.getHours()}:${d.getMinutes().toString().padStart(2,'0')}`;
							},
							autoSkip: false,
							maxRotation: 0
						},
						min: Date.now() - HOUR_MS,
						max: Date.now()
					},
					y: yScale
				}
			}
		});
	});

	// Fetch and draw the last-hour data, filling 5s gaps
	async function loadHour() {
		let json;
		try {
			const res = await fetch('/api/timestamp_metrics');
			if (!res.ok) throw res.status;
			json = await res.json();
		} catch (e) {
			console.error('hour_metrics load failed', e);
			return;
		}

		const now     = Date.now();
		const startTs = Math.ceil((now - HOUR_MS) / INTERVAL) * INTERVAL;

		METRICS.forEach(m => {
			const raw    = json[m.key].map(p => ({ x: p.x * 1000, y: p.y }));
			const lookup = Object.fromEntries(raw.map(p => [p.x, p.y]));
			const pts    = [];

			for (let ts = startTs; ts <= now; ts += INTERVAL) {
				pts.push({ x: ts, y: lookup[ts] ?? null });
			}

			const c = charts[m.key];
			c.data.datasets[0].data   = pts;
			c.options.scales.x.min    = now - HOUR_MS;
			c.options.scales.x.max    = now;
			c.update();
		});
	}

	// Fetch and append latest point using the returned timestamp
	async function fetchLive() {
		let latest;
		try {
			const res = await fetch('/api/live_metrics');
			if (!res.ok) throw res.status;
			latest = await res.json();
		} catch (e) {
			console.error('live_metrics load failed', e);
			return;
		}

		const tsMs      = latest.timestamp * 1000;
		const alignedTs = Math.floor(tsMs / INTERVAL) * INTERVAL;
		const now       = alignedTs;

		METRICS.forEach(m => {
			const c = charts[m.key];
			c.data.datasets[0].data = c.data.datasets[0].data.filter(p => p.x !== alignedTs);
			c.data.datasets[0].data.push({ x: alignedTs, y: latest[m.key] });
			c.data.datasets[0].data = c.data.datasets[0].data.filter(p => p.x >= now - HOUR_MS);
			c.options.scales.x.min = now - HOUR_MS;
			c.options.scales.x.max = now;
			c.update('none');
		});
	}

	// Draw hour, then start live updates after 5s.
	loadHour().then(() => {
		setTimeout(fetchLive, INTERVAL);
		setInterval(fetchLive, INTERVAL);
	});
	// Redraw on focus.
	window.addEventListener('focus', loadHour);
});
