(function() {
	const units = [
		{ label: 'y', seconds: 31536000 },
		{ label: 'd', seconds: 86400 },
		{ label: 'h', seconds: 3600 },
		{ label: 'm', seconds: 60 },
		{ label: 's', seconds: 1 }
	];

	const iconEl  = document.getElementById('uptime-icon');
	const valueEl = document.getElementById('uptime-value');

	const isDarkMode   = () => window.matchMedia('(prefers-color-scheme: dark)').matches;
	const getIconPath  = status => `/static/icons/${status}-${isDarkMode() ? 'dark' : 'light'}.png`;

	function formatUptime(sec) {
		let remaining = sec;
		const parts = [];

		for (const { label, seconds } of units) {
			const count = Math.floor(remaining / seconds);
			if (count > 0 || parts.length) {
				parts.push(`${count}${label}`);
				remaining %= seconds;
			}
			if (parts.length === 2) break;
		}

		return parts.length ? parts.join(' ') : '0s';
	}

	const uptime = {
		seconds: 0,
		reachable: false,

		async fetch() {
			try {
				const res = await fetch('/api/uptime', { cache: 'no-store' });
				if (!res.ok) throw new Error();
				const { uptime_seconds } = await res.json();
				this.seconds   = uptime_seconds;
				this.reachable = true;
			} catch {
				this.reachable = false;
			}
			this.update();
		},

		tick() {
			if (this.reachable) {
				this.seconds++;
				this.update();
			}
		},

		update() {
			const status = this.reachable ? 'online' : 'offline';
			iconEl.src = getIconPath(status);

			if (this.reachable) {
				valueEl.textContent = ` ${formatUptime(this.seconds)}`;
				valueEl.classList.remove('unreachable');
			} else {
				valueEl.textContent = 'Server unreachable';
				valueEl.classList.add('unreachable');
			}
		}
	};

	// kick off
	uptime.fetch();
	setInterval(() => uptime.tick(), 1000);

	// refresh on focus
	window.addEventListener('focus', () => uptime.fetch());

	// swap icon if dark/light preference changes
	window.matchMedia('(prefers-color-scheme: dark)')
	      .addEventListener('change', () => uptime.update());
})();