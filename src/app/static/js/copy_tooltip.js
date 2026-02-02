(() => {
	"use strict";

	const DEFAULT_TIMEOUT_MS = 1200;

	async function copyTextWithToast(text, container, tooltipEl, timeoutMs = DEFAULT_TIMEOUT_MS) {
		if (!text || !container) return;
		const finish = () => {
			container.classList.add("is-copied");
			if (tooltipEl) {
				tooltipEl.textContent = "Copied";
				tooltipEl.setAttribute("aria-hidden", "false");
			}
			setTimeout(() => container.classList.remove("is-copied"), timeoutMs);
			if (tooltipEl) {
				setTimeout(() => tooltipEl.setAttribute("aria-hidden", "true"), timeoutMs);
			}
		};

		try {
			if (navigator.clipboard && navigator.clipboard.writeText) {
				await navigator.clipboard.writeText(text);
				finish();
				return;
			}
		} catch {
			// fallback below
		}

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

	window.copyTextWithToast = copyTextWithToast;
})();
