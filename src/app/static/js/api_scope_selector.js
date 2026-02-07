(() => {
	"use strict";

	function initScopeSelector(root) {
		function cssEscape(s) {
			if (window.CSS && typeof window.CSS.escape === "function") {
				return window.CSS.escape(s);
			}
			return String(s).replace(/["\\#.:,[\]()>+~*^$|=\s]/g, "\\$&");
		}

		const targetName = root.dataset.targetInput || "requested_scopes";
		const hidden = root.querySelector(`input[name="${targetName}"]`);
		const selectedBox = root.querySelector("[data-scope-selected]");
		const emptyState = root.querySelector("[data-scope-empty]");
		const dropdown = root.querySelector("[data-scope-dropdown]");
		if (!hidden || !selectedBox || !dropdown) return;

		const optionOrder = Array.from(dropdown.querySelectorAll("option"))
			.map((opt) => opt.value)
			.filter(Boolean);
		const selectedValues = [];

		function syncHidden() {
			hidden.value = selectedValues.join(",");
		}

		function updateEmptyState() {
			if (!emptyState) return;
			emptyState.hidden = selectedValues.length > 0;
		}

		function removeFromDropdown(value) {
			const opt = dropdown.querySelector(`option[value="${cssEscape(value)}"]`);
			if (opt) opt.remove();
			dropdown.value = "";
		}

		function addToDropdown(value, label) {
			const exists = dropdown.querySelector(`option[value="${cssEscape(value)}"]`);
			if (exists) return;
			const option = document.createElement("option");
			option.value = value;
			option.textContent = label;
			const rank = optionOrder.indexOf(value);
			const options = Array.from(dropdown.querySelectorAll("option")).filter((o) => o.value);
			const insertBefore = options.find((o) => optionOrder.indexOf(o.value) > rank);
			if (insertBefore) {
				dropdown.insertBefore(option, insertBefore);
			} else {
				dropdown.appendChild(option);
			}
		}

		function removeChip(value, label, chip) {
			const idx = selectedValues.indexOf(value);
			if (idx >= 0) selectedValues.splice(idx, 1);
			addToDropdown(value, label);
			chip.remove();
			syncHidden();
			updateEmptyState();
		}

		function addChip(value, label) {
			if (!value || selectedValues.includes(value)) return;
			selectedValues.push(value);
			removeFromDropdown(value);

			const chip = document.createElement("button");
			chip.type = "button";
			chip.className = "scope-selector__chip";
			chip.setAttribute("data-scope-chip", value);
			chip.textContent = label;
			chip.addEventListener("click", () => removeChip(value, label, chip));
			selectedBox.appendChild(chip);

			syncHidden();
			updateEmptyState();
		}

		dropdown.addEventListener("change", () => {
			const value = dropdown.value || "";
			if (!value) return;
			const label = value;
			addChip(value, label);
		});

		updateEmptyState();
	}

	document.addEventListener("DOMContentLoaded", () => {
		document.querySelectorAll("[data-scope-selector]").forEach(initScopeSelector);
	});
})();
