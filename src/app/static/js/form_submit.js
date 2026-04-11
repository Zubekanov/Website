// static/js/form_submit.js
//
// Supported wiring models:
// 1) Legacy button-driven submits:
//    <button data-submit-route data-submit-method data-submit-fields ...>
// 2) Form-root submits:
//    <form data-form-submit-route data-form-submit-method ...>
//
// When no explicit field list is provided, the nearest form is serialized by
// collecting all enabled named controls.

(() => {
	"use strict";

	function qs(root, selector) {
		return root.querySelector(selector);
	}

	function qsa(root, selector) {
		return Array.from(root.querySelectorAll(selector));
	}

	function closestForm(el) {
		return el && typeof el.closest === "function" ? el.closest("form") : null;
	}

	function parseCSV(value) {
		if (!value) return [];
		return value
			.split(",")
			.map((part) => part.trim())
			.filter(Boolean);
	}

	function parseBool(value) {
		if (typeof value !== "string") return false;
		const normalized = value.trim().toLowerCase();
		return normalized === "1" || normalized === "true" || normalized === "yes";
	}

	function cssEscape(value) {
		if (window.CSS && typeof window.CSS.escape === "function") {
			return window.CSS.escape(value);
		}
		return String(value).replace(/["\\#.:,[\]()>+~*^$|=\s]/g, "\\$&");
	}

	function getFieldElements(scope, fieldName) {
		const byName = qsa(scope, `[name="${cssEscape(fieldName)}"]`);
		if (byName.length > 0) return byName;

		const byId = qs(scope, `#${cssEscape(fieldName)}`);
		return byId ? [byId] : [];
	}

	function readElementValue(el) {
		const tag = (el.tagName || "").toLowerCase();
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

	function collectSpecifiedFields(scope, fieldNames) {
		const data = {};
		const missing = [];

		for (const fieldName of fieldNames) {
			const elements = getFieldElements(scope, fieldName).filter((el) => !el.disabled);

			if (elements.length === 0) {
				missing.push(fieldName);
				continue;
			}

			const radioGroup = elements.some((el) => (el.getAttribute("type") || "").toLowerCase() === "radio");
			if (radioGroup) {
				const checked = elements.find((el) => el.checked);
				data[fieldName] = checked ? checked.value : null;
				continue;
			}

			const checkboxGroup = elements.every((el) => (el.getAttribute("type") || "").toLowerCase() === "checkbox");
			if (checkboxGroup) {
				if (elements.length === 1) {
					data[fieldName] = !!elements[0].checked;
				} else {
					data[fieldName] = elements.filter((el) => el.checked).map((el) => el.value);
				}
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

	function collectAllNamedControls(scope) {
		const elements = scope instanceof HTMLFormElement
			? Array.from(scope.elements)
			: qsa(scope, "input[name], textarea[name], select[name]");
		const grouped = new Map();

		for (const el of elements) {
			if (!el || !el.name || el.disabled) continue;
			const tag = (el.tagName || "").toLowerCase();
			const type = (el.getAttribute("type") || "").toLowerCase();
			if (tag === "button" || type === "submit" || type === "button" || type === "reset" || type === "file") {
				continue;
			}
			if (!grouped.has(el.name)) grouped.set(el.name, []);
			grouped.get(el.name).push(el);
		}

		const data = {};
		for (const [name, group] of grouped.entries()) {
			const radioGroup = group.some((el) => (el.getAttribute("type") || "").toLowerCase() === "radio");
			if (radioGroup) {
				const checked = group.find((el) => el.checked);
				data[name] = checked ? checked.value : null;
				continue;
			}

			const checkboxGroup = group.every((el) => (el.getAttribute("type") || "").toLowerCase() === "checkbox");
			if (checkboxGroup) {
				if (group.length === 1) {
					data[name] = !!group[0].checked;
				} else {
					data[name] = group.filter((el) => el.checked).map((el) => el.value);
				}
				continue;
			}

			if (group.length > 1) {
				data[name] = group.map(readElementValue);
				continue;
			}

			data[name] = readElementValue(group[0]);
		}

		return { data, missing: [] };
	}

	async function safeReadJson(resp) {
		const contentType = resp.headers.get("content-type") || "";
		if (contentType.includes("application/json")) {
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

	function extractMessage(json, fallbackText) {
		if (json && typeof json.message === "string" && json.message.trim().length > 0) {
			return json.message.trim();
		}
		if (fallbackText && String(fallbackText).trim().length > 0) {
			return String(fallbackText).trim();
		}
		return "";
	}

	function findMessageArea(source, form) {
		if (form) {
			const withinForm = qs(form, "[data-form-message]");
			if (withinForm) return withinForm;
		}

		if (source && typeof source.closest === "function") {
			const nearby = source.closest("[data-form-message]");
			if (nearby) return nearby;
		}

		return qs(document, "[data-form-message]");
	}

	function setMessage(source, form, message, ok) {
		const area = findMessageArea(source, form);
		if (!area) return;

		if (!message) {
			area.textContent = "";
			area.dataset.state = "";
			area.hidden = true;
			return;
		}

		area.hidden = false;
		area.textContent = message;
		area.dataset.state = ok ? "success" : "error";
	}

	function setBusy(source, busy) {
		if (!source) return;
		source.disabled = !!busy;
		source.dataset.busy = busy ? "true" : "false";
	}

	function buildConfig(source, form) {
		const buttonData = source && source.dataset ? source.dataset : {};
		const formData = form && form.dataset ? form.dataset : {};
		return {
			route: buttonData.submitRoute || formData.formSubmitRoute || "",
			method: (buttonData.submitMethod || formData.formSubmitMethod || "POST").toUpperCase(),
			fieldNames: parseCSV(buttonData.submitFields || formData.formSubmitFields || ""),
			successRedirect: buttonData.successRedirect ?? formData.formSuccessRedirect,
			failureRedirect: buttonData.failureRedirect ?? formData.formFailureRedirect,
			successRefresh: parseBool(buttonData.successRefresh || formData.formSuccessRefresh || ""),
			failureRefresh: parseBool(buttonData.failureRefresh || formData.formFailureRefresh || ""),
		};
	}

	function resolveRedirect(config, json, ok) {
		if (json && typeof json.redirect === "string") {
			return json.redirect;
		}
		return ok ? config.successRedirect : config.failureRedirect;
	}

	function handleNavigation(config, json, ok) {
		const redirect = resolveRedirect(config, json, ok);
		if (typeof redirect === "string" && redirect.trim().length > 0) {
			window.location.assign(redirect);
			return true;
		}

		if (ok && config.successRefresh) {
			window.location.reload();
			return true;
		}

		if (!ok && config.failureRefresh) {
			window.location.reload();
			return true;
		}

		return false;
	}

	function collectPayload(form, source, config) {
		const scope = form || document;
		if (config.fieldNames.length > 0) {
			return collectSpecifiedFields(scope, config.fieldNames);
		}
		return collectAllNamedControls(scope);
	}

	async function handleSubmission(source, form) {
		const config = buildConfig(source, form);
		if (!config.route) {
			setMessage(source, form, "Missing submit route.", false);
			return;
		}

		setMessage(source, form, "", false);

		const { data, missing } = collectPayload(form, source, config);
		if (missing.length > 0) {
			setMessage(source, form, `Missing fields: ${missing.join(", ")}.`, false);
			handleNavigation(config, null, false);
			return;
		}

		setBusy(source, true);
		try {
			let url = config.route;
			const fetchOptions = {
				method: config.method,
				headers: {
					accept: "application/json",
				},
			};

			if (config.method === "GET") {
				const params = new URLSearchParams();
				for (const [key, value] of Object.entries(data)) {
					if (Array.isArray(value)) {
						for (const item of value) {
							params.append(key, String(item));
						}
					} else if (value !== undefined && value !== null) {
						params.set(key, String(value));
					}
				}
				const serialized = params.toString();
				if (serialized) {
					const separator = url.includes("?") ? "&" : "?";
					url = `${url}${separator}${serialized}`;
				}
			} else {
				fetchOptions.headers["content-type"] = "application/json";
				fetchOptions.body = JSON.stringify(data);
			}

			const resp = await fetch(url, fetchOptions);
			const json = await safeReadJson(resp);
			const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;
			const navigated = handleNavigation(config, json, ok);

			if (!navigated) {
				setMessage(source, form, extractMessage(json, resp.statusText), ok);
			}
		} catch (err) {
			const navigated = handleNavigation(config, null, false);
			if (!navigated) {
				setMessage(source, form, String(err), false);
			}
		} finally {
			setBusy(source, false);
		}
	}

	document.addEventListener("click", (event) => {
		const button = event.target.closest("button[data-submit-route], input[data-submit-route]");
		if (!button) return;

		event.preventDefault();
		handleSubmission(button, closestForm(button));
	});

	document.addEventListener("submit", (event) => {
		const form = event.target;
		if (!(form instanceof HTMLFormElement)) return;

		const submitter = event.submitter || qs(form, "button[data-submit-route], input[data-submit-route], button[type='submit'], input[type='submit']");
		const hasFormAction = !!(form.dataset && form.dataset.formSubmitRoute);
		const hasSubmitterAction = !!(submitter && submitter.dataset && submitter.dataset.submitRoute);
		if (!hasFormAction && !hasSubmitterAction) return;

		event.preventDefault();
		handleSubmission(submitter || form, form);
	});

	document.addEventListener("keydown", (event) => {
		if (event.key !== "Enter") return;

		const target = event.target;
		if (!target) return;

		const tag = target.tagName ? target.tagName.toLowerCase() : "";
		if (tag !== "input" && tag !== "select") return;

		const form = closestForm(target);
		if (!form) return;

		const legacyButton = qs(form, "button[data-submit-route], input[data-submit-route]");
		if (!legacyButton) return;

		const hasSubmitType = !!qs(form, "button[type='submit'], input[type='submit']");
		if (hasSubmitType || (form.dataset && form.dataset.formSubmitRoute)) return;

		event.preventDefault();
		handleSubmission(legacyButton, form);
	});
})();
