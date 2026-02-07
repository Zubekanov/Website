(() => {
	"use strict";

	async function handleAction(button) {
		const route = button.dataset.submitRoute || "";
		const method = (button.dataset.submitMethod || "POST").toUpperCase();
		const id = button.dataset.requestId || "";
		if (!route || !id) return;

		const resp = await fetch(route, {
			method,
			headers: {
				"accept": "application/json",
				"content-type": "application/json",
			},
			body: JSON.stringify({ id }),
		});
		const json = await resp.json().catch(() => null);
		const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;

		if (ok) {
			const card = button.closest("[data-approval-card]");
			if (card) card.remove();
			return;
		}

		const msg = (json && json.message) || resp.statusText || "Request failed.";
		window.alert(msg);
	}

	document.addEventListener("click", (e) => {
		const button = e.target.closest("[data-approval-action]");
		if (!button) return;
		e.preventDefault();
		handleAction(button);
	});
})();
