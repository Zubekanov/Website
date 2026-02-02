(() => {
	"use strict";

	async function fetchCount(url) {
		const resp = await fetch(url);
		const json = await resp.json().catch(() => null);
		if (!json || typeof json.count !== "number") return null;
		return json.count;
	}

	function renderBadge(el, count) {
		el.classList.remove("admin-badge--loading");
		const capped = count > 99 ? "99+" : String(count);
		el.textContent = capped;
		el.classList.toggle("is-alert", count > 0);
	}

	function setLoading(el) {
		el.classList.add("admin-badge--loading");
		el.textContent = "";
	}

	async function initBadges() {
		const badgeA = document.querySelector('[data-badge="audiobookshelf"]');
		const badgeW = document.querySelector('[data-badge="discord-webhook"]');
		const badgeM = document.querySelector('[data-badge="minecraft"]');

		if (badgeA) {
			setLoading(badgeA);
			const count = await fetchCount("/api/admin/audiobookshelf/pending-count");
			if (count != null) renderBadge(badgeA, count);
		}
		if (badgeW) {
			setLoading(badgeW);
			const count = await fetchCount("/api/admin/discord-webhook/pending-count");
			if (count != null) renderBadge(badgeW, count);
		}
		if (badgeM) {
			setLoading(badgeM);
			const count = await fetchCount("/api/admin/minecraft/pending-count");
			if (count != null) renderBadge(badgeM, count);
		}
	}

	document.addEventListener("DOMContentLoaded", initBadges);
})();
