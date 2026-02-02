(() => {
	"use strict";

	function qs(root, selector) {
		return root.querySelector(selector);
	}

	function qsa(root, selector) {
		return Array.from(root.querySelectorAll(selector));
	}

	function parseCSV(value) {
		if (!value) return [];
		return value
			.split(",")
			.map((s) => s.trim())
			.filter(Boolean);
	}

	function readElementValue(el) {
		const tag = el.tagName.toLowerCase();
		const type = (el.getAttribute("type") || "").toLowerCase();

		if (tag === "select" || tag === "textarea") {
			return el.value;
		}

		if (type === "checkbox") {
			return !!el.checked;
		}

		if (type === "radio") {
			return el.checked ? el.value : null;
		}

		return el.value;
	}

	function collectFields(scope, fieldNames) {
		const data = {};
		for (const fieldName of fieldNames) {
			const elements = qsa(scope, `[name="${CSS.escape(fieldName)}"]`);
			if (elements.length === 0) {
				data[fieldName] = null;
				continue;
			}
			if (elements.length > 1) {
				data[fieldName] = elements.map(readElementValue);
			} else {
				data[fieldName] = readElementValue(elements[0]);
			}
		}
		return data;
	}

	async function safeReadJson(resp) {
		const ct = resp.headers.get("content-type") || "";
		if (ct.includes("application/json")) {
			try {
				return await resp.json();
			} catch {
				return null;
			}
		}
		try {
			return await resp.json();
		} catch {
			return null;
		}
	}

	function findMessageArea(button) {
		const form = button.closest("form");
		if (form) {
			const withinForm = qs(form, "[data-form-message]");
			if (withinForm) return withinForm;
		}
		return qs(document, "[data-form-message]");
	}

	function setMessage(button, msg, ok) {
		const area = findMessageArea(button);
		if (!area) return;
		if (!msg) {
			area.textContent = "";
			area.hidden = true;
			area.dataset.state = "";
			return;
		}
		area.hidden = false;
		area.textContent = msg;
		area.dataset.state = ok ? "success" : "error";
	}

	function buildInput(colName, colType, value) {
		const cell = document.createElement("div");
		cell.className = "db-cell";
		if (colType === "boolean") {
			cell.classList.add("db-cell--checkbox");
			const input = document.createElement("input");
			input.className = "db-form-input db-form-input--checkbox";
			input.type = "checkbox";
			input.name = `col__${colName}`;
			input.checked = !!value;
			cell.appendChild(input);
		} else {
			const input = document.createElement("input");
			input.className = "db-form-input";
			input.type = "text";
			input.name = `col__${colName}`;
			input.value = value == null ? "" : String(value);
			cell.appendChild(input);
		}
		return cell;
	}

	function buildHidden(name, value) {
		const input = document.createElement("input");
		input.type = "hidden";
		input.name = name;
		input.className = "db-hidden";
		input.value = value == null ? "" : String(value);
		return input;
	}

	function buildActions(fieldsAttr) {
		const cell = document.createElement("div");
		cell.className = "db-cell db-cell--actions";
		const actions = document.createElement("div");
		actions.className = "db-actions";

		const save = document.createElement("button");
		save.type = "submit";
		save.className = "db-btn";
		save.dataset.dbAction = "update";
		save.dataset.submitRoute = "/api/admin/db/update-row";
		save.dataset.submitMethod = "POST";
		if (fieldsAttr) save.dataset.submitFields = fieldsAttr;
		save.textContent = "Save";

		const del = document.createElement("button");
		del.type = "submit";
		del.className = "db-btn db-btn--danger";
		del.dataset.dbAction = "delete";
		del.dataset.submitRoute = "/api/admin/db/delete-row";
		del.dataset.submitMethod = "POST";
		if (fieldsAttr) del.dataset.submitFields = fieldsAttr;
		del.textContent = "Delete";

		actions.appendChild(save);
		actions.appendChild(del);
		cell.appendChild(actions);
		return cell;
	}

	function insertRow(grid, row) {
		const cols = parseCSV(grid.dataset.columns || "");
		const types = parseCSV(grid.dataset.colTypes || "");
		const pkCols = parseCSV(grid.dataset.pkCols || "");

		const form = document.createElement("form");
		form.className = "db-grid-row db-row-form db-row-form--data form";

		form.appendChild(buildHidden("table", row.__table));
		form.appendChild(buildHidden("schema", row.__schema));
		for (const pk of pkCols) {
			form.appendChild(buildHidden(`pk__${pk}`, row[pk]));
		}

		for (let i = 0; i < cols.length; i++) {
			const colName = cols[i];
			const colType = (types[i] || "").toLowerCase();
			const cell = buildInput(colName, colType, row[colName]);
			cell.dataset.colIndex = String(i);
			form.appendChild(cell);
		}

		const fields = ["table", "schema"];
		fields.push(...pkCols.map((c) => `pk__${c}`));
		fields.push(...cols.map((c) => `col__${c}`));
		form.appendChild(buildActions(fields.join(", ")));

		const addHead = grid.querySelector(".db-add-row-head");
		if (addHead) {
			grid.insertBefore(form, addHead);
		} else {
			grid.appendChild(form);
		}

		// Ensure new buttons match width sizing logic.
		const buttons = Array.from(document.querySelectorAll(".db-btn"));
		if (buttons.length > 0) {
			const max = Math.max(...buttons.map((b) => b.getBoundingClientRect().width));
			buttons.forEach((b) => {
				b.style.width = `${Math.ceil(max)}px`;
			});
		}
	}

	async function handleAction(button) {
		const form = button.closest("form");
		if (!form) return;

		const route = button.dataset.submitRoute || "";
		const method = (button.dataset.submitMethod || "POST").toUpperCase();
		const fields = parseCSV(button.dataset.submitFields || "");
		const data = collectFields(form, fields);

		const resp = await fetch(route, {
			method,
			headers: {
				"accept": "application/json",
				"content-type": "application/json",
			},
			body: JSON.stringify(data),
		});
		const json = await safeReadJson(resp);
		const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;

		if (!ok) {
			setMessage(button, (json && json.message) || resp.statusText, false);
			return;
		}

		const action = button.dataset.dbAction;
		if (action === "delete") {
			form.remove();
		} else if (action === "add") {
			const grid = button.closest(".db-grid");
			if (grid && json && json.row) {
				const row = { ...json.row };
				row.__table = data.table;
				row.__schema = data.schema || "public";
				insertRow(grid, row);
				form.reset();
			}
		}

		setMessage(button, (json && json.message) || "OK", true);
	}

	document.addEventListener(
		"click",
		(e) => {
			const button = e.target.closest("button[data-db-action][data-submit-route]");
			if (!button) return;
			e.preventDefault();
			e.stopImmediatePropagation();
			handleAction(button);
		},
		true
	);
})();
