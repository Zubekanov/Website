(() => {
	"use strict";

	let tooltipEl = null;

	function ensureTooltip() {
		if (tooltipEl) return tooltipEl;
		tooltipEl = document.createElement("div");
		tooltipEl.className = "profile-popu-tooltip";
		tooltipEl.setAttribute("aria-hidden", "true");
		tooltipEl.style.display = "none";
		document.body.appendChild(tooltipEl);
		return tooltipEl;
	}

	function hideTooltip() {
		if (!tooltipEl) return;
		tooltipEl.style.opacity = "0";
		tooltipEl.style.display = "none";
	}

	function placeTooltip(clientX, clientY) {
		if (!tooltipEl) return;
		const pad = 10;
		const viewportW = (window.visualViewport && window.visualViewport.width) || window.innerWidth;
		const viewportH = (window.visualViewport && window.visualViewport.height) || window.innerHeight;
		const rect = tooltipEl.getBoundingClientRect();
		let x = clientX + 12;
		let y = clientY - rect.height - 10;
		if (x + rect.width + pad > viewportW) x = viewportW - rect.width - pad;
		if (x < pad) x = pad;
		if (y < pad) y = clientY + 14;
		if (y + rect.height + pad > viewportH) y = viewportH - rect.height - pad;
		tooltipEl.style.left = `${Math.round(x)}px`;
		tooltipEl.style.top = `${Math.round(y)}px`;
		tooltipEl.style.transform = "none";
	}

	function showTooltip(text, clientX, clientY) {
		const el = ensureTooltip();
		const viewportMax = Math.max(160, window.innerWidth - 20);
		el.style.maxWidth = `${Math.min(420, viewportMax)}px`;
		el.style.whiteSpace = "normal";
		el.style.overflowWrap = "anywhere";
		el.style.wordBreak = "break-word";
		el.textContent = text || "";
		el.style.display = "block";
		el.style.left = "0px";
		el.style.top = "0px";
		el.style.opacity = "1";
		placeTooltip(clientX, clientY);
		window.requestAnimationFrame(() => placeTooltip(clientX, clientY));
	}

	function parsePx(v, fallback = 0) {
		const n = Number.parseFloat(v || "");
		return Number.isFinite(n) ? n : fallback;
	}

	function collectPlayedBoxes(historyEl) {
		return Array.from(historyEl.querySelectorAll(".profile-popu-box[data-played=\"1\"]")).map((el) => ({
			outcome: el.dataset.outcome || "draw",
			tooltip: el.dataset.tooltip || "",
		}));
	}

	function makeBox({ outcome, tooltip, played }) {
		const box = document.createElement("span");
		box.className = `profile-popu-box profile-popu-box--${outcome}`;
		if (played) {
			box.dataset.played = "1";
			box.dataset.outcome = outcome;
			box.dataset.tooltip = tooltip;
			box.setAttribute("aria-label", tooltip);
		} else {
			box.dataset.tooltip = "No game";
			box.setAttribute("aria-label", "No game");
		}
		return box;
	}

	function renderResponsive(historyEl, played) {
		const style = window.getComputedStyle(historyEl);
		const gap = parsePx(style.columnGap || style.gap, 6);
		const boxSize = parsePx(style.getPropertyValue("--popu-box-size"), 18);
		const width = historyEl.clientWidth || 0;
		const slot = Math.max(1, Math.floor((width + gap) / (boxSize + gap)));

		historyEl.style.setProperty("--popu-history-cols", String(slot));
		const visiblePlayed = played.slice(-slot);
		const empties = Math.max(0, slot - visiblePlayed.length);
		const all = [];
		for (let i = 0; i < empties; i += 1) all.push({ outcome: "empty", tooltip: "No game", played: false });
		for (const p of visiblePlayed) all.push({ outcome: p.outcome, tooltip: p.tooltip, played: true });

		historyEl.innerHTML = "";
		for (const entry of all) historyEl.appendChild(makeBox(entry));
	}

	function initHistory(historyEl) {
		const played = collectPlayedBoxes(historyEl);
		renderResponsive(historyEl, played);
		const ro = new ResizeObserver(() => renderResponsive(historyEl, played));
		ro.observe(historyEl);

		historyEl.addEventListener("mouseenter", (e) => {
			const box = e.target.closest(".profile-popu-box[data-played=\"1\"]");
			if (!box) return;
			showTooltip(box.dataset.tooltip || "", e.clientX, e.clientY);
		}, true);
		historyEl.addEventListener("mousemove", (e) => {
			const box = e.target.closest(".profile-popu-box[data-played=\"1\"]");
			if (!box) {
				hideTooltip();
				return;
			}
			showTooltip(box.dataset.tooltip || "", e.clientX, e.clientY);
		});
		historyEl.addEventListener("mouseleave", hideTooltip, true);
	}

	document.addEventListener("DOMContentLoaded", () => {
		document.querySelectorAll("[data-popu-history]").forEach(initHistory);
	});
})();
