(() => {
	"use strict";

	const dataEl = document.getElementById("user-id-options-data");
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

	const dropdown = document.createElement("div");
	dropdown.className = "db-user-id-dropdown";
	dropdown.hidden = true;
	document.body.appendChild(dropdown);

	let activeInput = null;
	let blurTimer = null;

	const escapeHtml = (s) => String(s).replace(/[&<>"]|'/g, (m) => ({
		"&": "&amp;",
		"<": "&lt;",
		">": "&gt;",
		"\"": "&quot;",
		"'": "&#39;",
	}[m]));

	const positionDropdown = (input) => {
		const rect = input.getBoundingClientRect();
		const maxWidth = Math.min(520, window.innerWidth - 40);
		const desired = Math.max(rect.width, 320);
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
			if (newWidth >= 200) {
				width = newWidth;
			} else {
				width = Math.max(200, window.innerWidth - 40);
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
			const label = (opt.label || "").toLowerCase();
			const id = (opt.id || "").toLowerCase();
			return !query || label.includes(query) || id.includes(query);
		}).slice(0, 8);

		if (filtered.length === 0) {
			dropdown.hidden = true;
			return;
		}

		dropdown.innerHTML = filtered.map((opt) => (
			`<div class="db-user-id-option" data-user-id="${escapeHtml(opt.id)}">` +
			`<div class="label">${escapeHtml(opt.label || opt.id)}</div>` +
			`<div class="meta">${escapeHtml(opt.id)}</div>` +
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
		const option = e.target.closest(".db-user-id-option");
		if (!option || !activeInput) return;
		e.preventDefault();
		activeInput.value = option.getAttribute("data-user-id") || "";
		hideDropdown();
	});

	const inputs = Array.from(document.querySelectorAll("input[data-user-id-input]"));
	inputs.forEach((input) => {
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
