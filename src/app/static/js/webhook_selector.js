(() => {
	"use strict";

	const dataEl = document.getElementById("webhook-options-data");
	if (!dataEl) return;

	let options = [];
	try {
		const raw = dataEl.textContent || "";
		const encoding = dataEl.getAttribute("data-encoding");
		const jsonText = encoding === "base64" ? atob(raw.trim()) : raw;
		options = JSON.parse(jsonText || "[]");
	} catch {
		options = [];
	}
	if (!Array.isArray(options) || options.length === 0) return;

	const nameInput = document.querySelector("input[data-webhook-input='name']");
	const urlInput = document.querySelector("input[data-webhook-input='url']");
	if (!nameInput || !urlInput) return;

	const dropdown = document.createElement("div");
	dropdown.className = "webhook-selector-dropdown";
	dropdown.hidden = true;
	document.body.appendChild(dropdown);

	let activeInput = null;
	let blurTimer = null;

	const escapeHtml = (s) => String(s).replace(/[&<>\"]|'/g, (m) => ({
		"&": "&amp;",
		"<": "&lt;",
		">": "&gt;",
		"\"": "&quot;",
		"'": "&#39;",
	}[m]));

	const positionDropdown = (input) => {
		const rect = input.getBoundingClientRect();
		const maxWidth = Math.min(560, window.innerWidth - 40);
		const desired = Math.max(rect.width, 360);
		let width = Math.min(desired, maxWidth);
		const scrollX = window.scrollX;
		let left = rect.left + scrollX;
		const minLeft = scrollX + 20;
		const maxLeft = scrollX + window.innerWidth - width - 20;
		if (left > maxLeft) left = maxLeft;
		if (left < minLeft) left = minLeft;
		const viewportRight = scrollX + window.innerWidth - 20;
		if (left + width > viewportRight) {
			const newWidth = viewportRight - left;
			if (newWidth >= 240) {
				width = newWidth;
			} else {
				width = Math.max(240, window.innerWidth - 40);
				left = minLeft;
			}
		}
		dropdown.style.left = `${left}px`;
		dropdown.style.top = `${rect.bottom + window.scrollY + 4}px`;
		dropdown.style.width = `${width}px`;
	};

	const renderOptions = (input) => {
		const query = (input.value || "").toLowerCase().trim();
		const filtered = options.filter((opt) => {
			const name = (opt.name || "").toLowerCase();
			const url = (opt.url || "").toLowerCase();
			return !query || name.includes(query) || url.includes(query);
		}).slice(0, 8);

		if (filtered.length === 0) {
			dropdown.hidden = true;
			return;
		}

		dropdown.innerHTML = filtered.map((opt) => (
			`<div class="webhook-selector-option" data-webhook-name="${escapeHtml(opt.name || "")}" data-webhook-url="${escapeHtml(opt.url || "")}">` +
			`<div class="label">${escapeHtml(opt.name || opt.url || "Webhook")}</div>` +
			`<div class="meta">${escapeHtml(opt.url || "")}</div>` +
			`</div>`
		)).join("");

		dropdown.hidden = false;
		positionDropdown(input);
	};

	const showDropdown = (input) => {
		activeInput = input;
		renderOptions(input);
	};

	const hideDropdown = () => {
		dropdown.hidden = true;
		activeInput = null;
	};

	dropdown.addEventListener("mousedown", (e) => {
		const option = e.target.closest(".webhook-selector-option");
		if (!option) return;
		e.preventDefault();
		const name = option.getAttribute("data-webhook-name") || "";
		const url = option.getAttribute("data-webhook-url") || "";
		nameInput.value = name;
		urlInput.value = url;
		hideDropdown();
	});

	[nameInput, urlInput].forEach((input) => {
		input.setAttribute("autocomplete", "off");
		input.addEventListener("focus", () => {
			if (blurTimer) {
				clearTimeout(blurTimer);
				blurTimer = null;
			}
			showDropdown(input);
		});
		input.addEventListener("input", () => showDropdown(input));
		input.addEventListener("blur", () => {
			blurTimer = setTimeout(() => hideDropdown(), 120);
		});
	});

	window.addEventListener("scroll", () => {
		if (activeInput) positionDropdown(activeInput);
	}, true);

	window.addEventListener("resize", () => {
		if (activeInput) positionDropdown(activeInput);
	});
})();
