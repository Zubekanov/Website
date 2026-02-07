document.addEventListener("DOMContentLoaded", () => {
	const tickerBanners = document.querySelectorAll(".alert-banner.ticker");
	tickerBanners.forEach(banner => initTickerBanner(banner));
});

function initTickerBanner(banner) {
	const container = banner.querySelector(".alert-ticker");
	if (!container) return;

	const seg1 = container.querySelector('[data-segment="1"]');
	const seg2 = container.querySelector('[data-segment="2"]');
	if (!seg1 || !seg2) return;

	const baseHTML = seg1.innerHTML;
	const speed = parseFloat(banner.dataset.speed || "60"); // px/s

	function ensureWidth() {
		seg1.innerHTML = baseHTML;

		let segWidth = seg1.getBoundingClientRect().width;
		const containerWidth = container.getBoundingClientRect().width;

		while (segWidth < containerWidth) {
			seg1.insertAdjacentHTML("beforeend", baseHTML);
			segWidth = seg1.getBoundingClientRect().width;
		}

		seg2.innerHTML = seg1.innerHTML;

		const segHeight = seg1.getBoundingClientRect().height;
		container.style.height = `${segHeight}px`;

		return segWidth;
	}

	let segWidth = ensureWidth();

	seg1.style.position = "absolute";
	seg2.style.position = "absolute";
	seg1.style.top = "0";
	seg2.style.top = "0";

	let x1 = 0;
	let x2 = segWidth;
	let lastTime = null;
	let rafId = null;

	function applyTransforms() {
		seg1.style.transform = `translateX(${x1}px)`;
		seg2.style.transform = `translateX(${x2}px)`;
	}

	function step(ts) {
		if (lastTime == null) {
			lastTime = ts;
			rafId = requestAnimationFrame(step);
			return;
		}

		const dt = (ts - lastTime) / 1000;
		lastTime = ts;

		const dx = speed * dt;
		x1 -= dx;
		x2 -= dx;

		if (x1 <= -segWidth) {
			x1 = x2 + segWidth;
		}
		if (x2 <= -segWidth) {
			x2 = x1 + segWidth;
		}

		applyTransforms();
		rafId = requestAnimationFrame(step);
	}

	applyTransforms();
	rafId = requestAnimationFrame(step);

	let resizeTimeout = null;
	window.addEventListener("resize", () => {
		clearTimeout(resizeTimeout);
		resizeTimeout = setTimeout(() => {
			if (rafId != null) {
				cancelAnimationFrame(rafId);
				rafId = null;
			}

			segWidth = ensureWidth();
			x1 = 0;
			x2 = segWidth;
			lastTime = null;
			applyTransforms();
			rafId = requestAnimationFrame(step);
		}, 150);
	});
}
