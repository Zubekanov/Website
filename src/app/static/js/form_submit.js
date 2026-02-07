// static/js/form_submit.js
//
// Alterations:
// 1) Redirecting to "" should refresh the page.
//    - If redirect resolves to "" (empty string), we call window.location.reload().
// 2) If no redirect happens, show server message (if any) in the nearest
//    [data-form-message] element (or the button's form, else document-level).
//
// Server JSON convention supported:
//   { ok: bool, message?: string, redirect?: string }
// If server isn't JSON, we'll fall back to HTTP status text for messaging.

(() => {
	"use strict";

	function qs(root, selector) {
		return root.querySelector(selector);
	}

	function qsa(root, selector) {
		return Array.from(root.querySelectorAll(selector));
	}

	function closestForm(el) {
		return el.closest("form");
	}

	function parseCSV(value) {
		if (!value) return [];
		return value
			.split(",")
			.map(s => s.trim())
			.filter(Boolean);
	}

	function cssEscape(s) {
		if (window.CSS && typeof window.CSS.escape === "function") {
			return window.CSS.escape(s);
		}
		return String(s).replace(/["\\#.:,[\]()>+~*^$|=\s]/g, "\\$&");
	}

	function getFieldElements(scope, fieldName) {
		const byName = qsa(scope, `[name="${cssEscape(fieldName)}"]`);
		if (byName.length > 0) return byName;

		const byId = qs(scope, `#${cssEscape(fieldName)}`);
		return byId ? [byId] : [];
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
		const missing = [];

		for (const fieldName of fieldNames) {
			const elements = getFieldElements(scope, fieldName);

			if (elements.length === 0) {
				missing.push(fieldName);
				continue;
			}

			const isRadioGroup = elements.some(el => (el.getAttribute("type") || "").toLowerCase() === "radio");
			if (isRadioGroup) {
				const checked = elements.find(el => el.checked);
				data[fieldName] = checked ? checked.value : null;
				continue;
			}

			if (elements.length > 1) {
				data[fieldName] = elements.map(readElementValue);
				continue;
			}

			data[fieldName] = readElementValue(elements[0]);
		}

		return { data, missing };
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

	function resolveRedirect(button, json, ok) {
		if (json && typeof json.redirect === "string") {
			return json.redirect; // may be ""
		}
		if (ok) {
			return button.dataset.successRedirect ?? "";
		}
		return button.dataset.failureRedirect ?? "";
	}

	function extractMessage(json, fallbackText) {
		if (json && typeof json.message === "string" && json.message.trim().length > 0) {
			return json.message.trim();
		}
		if (fallbackText && String(fallbackText).trim().length > 0) {
			return String(fallbackText).trim();
		}
		return "";
	}

	function findMessageArea(button) {
		// Prefer nearest form, then nearest ancestor, then document
		const form = closestForm(button);
		if (form) {
			const withinForm = qs(form, "[data-form-message]");
			if (withinForm) return withinForm;
		}

		const nearby = button.closest("[data-form-message]");
		if (nearby) return nearby;

		return qs(document, "[data-form-message]");
	}

	function setMessage(button, msg, ok) {
		const area = findMessageArea(button);
		if (!area) return;

		if (!msg) {
			area.textContent = "";
			area.dataset.state = "";
			area.hidden = true;
			return;
		}

		area.hidden = false;
		area.textContent = msg;
		area.dataset.state = ok ? "success" : "error";
	}

	function setBusy(button, busy) {
		button.disabled = !!busy;
		button.dataset.busy = busy ? "true" : "false";
	}

	function doRedirect(redirect) {
        // Redirect ONLY if it is a non-empty string
        if (typeof redirect === "string" && redirect.trim().length > 0) {
            window.location.assign(redirect);
            return true;
        }
        return false;
    }

	async function handleSubmitClick(button) {
		const route = button.dataset.submitRoute || "";
		const method = (button.dataset.submitMethod || "POST").toUpperCase();
		const fieldNames = parseCSV(button.dataset.submitFields || "");

		if (!route) {
			setMessage(button, "Missing data-submit-route.", false);
			return;
		}

		// Clear existing message on new submit
		setMessage(button, "", false);

		const form = closestForm(button);
		const scope = form || document;

		const { data, missing } = collectFields(scope, fieldNames);

		if (missing.length > 0) {
			const msg = `Missing fields: ${missing.join(", ")}.`;
			setMessage(button, msg, false);

			const redirect = resolveRedirect(button, null, false);
			// If failure redirect is "", refresh
			doRedirect(redirect);
			return;
		}

		setBusy(button, true);

		try {
			let url = route;
			const fetchOpts = {
				method,
				headers: {
					"accept": "application/json",
				},
			};

			if (method === "GET") {
				const params = new URLSearchParams();
				for (const [k, v] of Object.entries(data)) {
					if (Array.isArray(v)) {
						for (const item of v) params.append(k, String(item));
					} else if (v !== undefined && v !== null) {
						params.set(k, String(v));
					}
				}
				const sep = url.includes("?") ? "&" : "?";
				url = `${url}${sep}${params.toString()}`;
			} else {
				fetchOpts.headers["content-type"] = "application/json";
				fetchOpts.body = JSON.stringify(data);
			}

			const resp = await fetch(url, fetchOpts);
			const json = await safeReadJson(resp);

			const ok = (json && typeof json.ok === "boolean") ? json.ok : resp.ok;

			const redirect = resolveRedirect(button, json, ok);
			const didRedirect = doRedirect(redirect);

			// If we didn't redirect, show message if present
			if (!didRedirect) {
				const msg = extractMessage(json, resp.statusText);
				setMessage(button, msg, ok);
			}
		} catch (err) {
			const redirect = resolveRedirect(button, null, false);
			const didRedirect = doRedirect(redirect);

			if (!didRedirect) {
				setMessage(button, String(err), false);
			}
		} finally {
			setBusy(button, false);
		}
	}

	document.addEventListener("click", (e) => {
		const button = e.target.closest('button[data-submit-route][data-submit-method]');
		if (!button) return;

		e.preventDefault();
		handleSubmitClick(button);
	});

	document.addEventListener("keydown", (e) => {
		if (e.key !== "Enter") return;

		const target = e.target;
		if (!target) return;

		const tag = target.tagName ? target.tagName.toLowerCase() : "";
		if (tag !== "input" && tag !== "textarea" && tag !== "select") return;

		const form = closestForm(target);
		if (!form) return;

		if (tag === "textarea") return;

		const button = qs(form, 'button[data-submit-route][data-submit-method]');
		if (!button) return;

		e.preventDefault();
		handleSubmitClick(button);
	});
})();
