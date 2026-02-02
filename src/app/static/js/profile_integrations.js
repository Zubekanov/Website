(() => {
	"use strict";

	async function handleAction(button) {
		const route = button.dataset.submitRoute || "";
		const subId = button.dataset.subscriptionId || "";
		if (!route || !subId) return;

		button.disabled = true;
		const resp = await fetch(route, {
			method: "POST",
			headers: {
				"accept": "application/json",
				"content-type": "application/json",
			},
			body: JSON.stringify({ subscription_id: subId }),
		});
		const json = await resp.json().catch(() => null);
		const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;
		if (!ok) {
			button.disabled = false;
			const msg = (json && json.message) || resp.statusText || "Request failed.";
			window.alert(msg);
			return;
		}

		const card = button.closest("[data-subscription-card]");
		if (!card) return;
		if (button.dataset.subscriptionAction === "unsubscribe") {
			const status = card.querySelector(".subscription-status");
			if (status) {
				status.textContent = "Inactive";
				status.classList.add("subscription-status--inactive");
			}
			const unsubscribeBtn = card.querySelector("[data-subscription-action='unsubscribe']");
			if (unsubscribeBtn) unsubscribeBtn.remove();
			const footer = card.querySelector(".subscription-footer");
			if (footer && !footer.querySelector("[data-subscription-action='resubscribe']")) {
				const resub = document.createElement("button");
				resub.className = "subscription-action";
				resub.dataset.subscriptionAction = "resubscribe";
				resub.dataset.subscriptionId = subId;
				resub.dataset.submitRoute = "/api/profile/discord-webhook/resubscribe";
				resub.textContent = "Resubscribe";
				footer.appendChild(resub);
			}
			return;
		}
		if (button.dataset.subscriptionAction === "resubscribe") {
			const status = card.querySelector(".subscription-status");
			if (status) {
				status.textContent = "Active";
				status.classList.remove("subscription-status--inactive");
			}
			const resubscribeBtn = card.querySelector("[data-subscription-action='resubscribe']");
			if (resubscribeBtn) resubscribeBtn.remove();
			const footer = card.querySelector(".subscription-footer");
			if (footer && !footer.querySelector("[data-subscription-action='unsubscribe']")) {
				const unsub = document.createElement("button");
				unsub.className = "subscription-action";
				unsub.dataset.subscriptionAction = "unsubscribe";
				unsub.dataset.subscriptionId = subId;
				unsub.dataset.submitRoute = "/api/profile/discord-webhook/unsubscribe";
				unsub.textContent = "Unsubscribe";
				footer.appendChild(unsub);
			}
		}
	}

	document.addEventListener("click", (event) => {
		const button = event.target.closest("[data-subscription-action]");
		if (!button) return;
		event.preventDefault();
		handleAction(button);
	});

	const modal = document.querySelector("[data-integration-modal]");
	if (!modal) return;
	const modalName = modal.querySelector("[data-integration-modal-name]");
	const modalMessage = modal.querySelector("[data-integration-modal-message]");
	const reasonSelect = modal.querySelector("[data-integration-reason]");
	const confirmCheckbox = modal.querySelector("[data-integration-confirm]");
	const submitBtn = modal.querySelector("[data-integration-submit]");
	const closeEls = modal.querySelectorAll("[data-integration-modal-close]");
	let activeIntegration = null;

	function openModal(target) {
		activeIntegration = target;
		if (modalName) modalName.textContent = target.dataset.integrationLabel || "this integration";
		if (modalMessage) modalMessage.textContent = "";
		if (reasonSelect) reasonSelect.value = "";
		if (confirmCheckbox) confirmCheckbox.checked = false;
		modal.hidden = false;
		document.body.classList.add("modal-open");
	}

	function closeModal() {
		modal.hidden = true;
		document.body.classList.remove("modal-open");
		activeIntegration = null;
	}

	closeEls.forEach((btn) => {
		btn.addEventListener("click", (e) => {
			e.preventDefault();
			closeModal();
		});
	});

	document.addEventListener("click", (event) => {
		const trigger = event.target.closest("[data-integration-delete]");
		if (!trigger) return;
		event.preventDefault();
		openModal(trigger);
	});

	if (submitBtn) {
		submitBtn.addEventListener("click", async (event) => {
			event.preventDefault();
			if (!activeIntegration) return;
			const reason = reasonSelect ? reasonSelect.value : "";
			const confirm = confirmCheckbox ? confirmCheckbox.checked : false;
			submitBtn.disabled = true;
			if (modalMessage) modalMessage.textContent = "";

			try {
				const resp = await fetch("/api/profile/integration/delete", {
					method: "POST",
					headers: {
						"accept": "application/json",
						"content-type": "application/json",
					},
					body: JSON.stringify({
						integration_type: activeIntegration.dataset.integrationType || "",
						integration_id: activeIntegration.dataset.integrationId || "",
						reason,
						confirm,
					}),
				});
				const json = await resp.json().catch(() => null);
				const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;
				if (!ok) {
					const msg = (json && json.message) || resp.statusText || "Delete failed.";
					if (modalMessage) modalMessage.textContent = msg;
					submitBtn.disabled = false;
					return;
				}

				const card = activeIntegration.closest("[data-integration-card]");
				if (card) {
					const badge = card.querySelector(".integration-badge");
					if (badge) {
						badge.textContent = "Suspended";
						badge.classList.add("integration-badge--inactive");
					}
					const delBtn = card.querySelector("[data-integration-delete]");
					if (delBtn) delBtn.remove();
				}
				closeModal();
			} catch (err) {
				if (modalMessage) modalMessage.textContent = String(err);
				submitBtn.disabled = false;
			}
		});
	}

	const passwordPanel = document.querySelector("[data-password-panel]");
	if (!passwordPanel) return;
	const passwordOpen = document.querySelector("[data-password-panel-toggle]");
	const passwordCloseEls = passwordPanel.querySelectorAll("[data-password-panel-close]");
	const passwordInput = passwordPanel.querySelector("[data-password-input]");
	const passwordConfirm = passwordPanel.querySelector("[data-password-confirm]");
	const passwordSubmit = passwordPanel.querySelector("[data-password-submit]");
	const passwordMessage = passwordPanel.querySelector("[data-password-message]");
	const ANIM_MS = 280;
	const card = passwordPanel.closest(".profile-card") || deletePanel?.closest(".profile-card");
	let holdTimer = null;

	function holdCardHeight() {
		if (!card) return;
		card.style.minHeight = `${card.offsetHeight}px`;
		if (holdTimer) {
			clearTimeout(holdTimer);
			holdTimer = null;
		}
	}

	function releaseCardHeight(delay = 0) {
		if (!card) return;
		if (holdTimer) {
			clearTimeout(holdTimer);
		}
		holdTimer = setTimeout(() => {
			card.style.minHeight = "";
		}, delay);
	}

	function openPasswordPanel() {
		if (passwordMessage) passwordMessage.textContent = "";
		if (passwordInput) passwordInput.value = "";
		if (passwordConfirm) passwordConfirm.value = "";
		passwordPanel.hidden = false;
		requestAnimationFrame(() => passwordPanel.classList.add("is-open"));
		if (passwordInput) passwordInput.focus();
	}

	function closePasswordPanel() {
		passwordPanel.classList.remove("is-open");
		setTimeout(() => {
			passwordPanel.hidden = true;
		}, ANIM_MS);
	}

	if (passwordOpen) {
		passwordOpen.addEventListener("click", (e) => {
			e.preventDefault();
			if (passwordPanel.hidden) {
				if (deletePanel && !deletePanel.hidden) {
					holdCardHeight();
					closeDeletePanel();
					setTimeout(() => {
						openPasswordPanel();
						releaseCardHeight(ANIM_MS);
					}, ANIM_MS);
				} else {
					openPasswordPanel();
				}
				return;
			}
			closePasswordPanel();
		});
	}

	passwordCloseEls.forEach((btn) => {
		btn.addEventListener("click", (e) => {
			e.preventDefault();
			closePasswordPanel();
		});
	});

	if (passwordSubmit) {
		passwordSubmit.addEventListener("click", async (e) => {
			e.preventDefault();
			if (passwordMessage) passwordMessage.textContent = "";
			const pwd = passwordInput ? passwordInput.value : "";
			const confirm = passwordConfirm ? passwordConfirm.value : "";
			passwordSubmit.disabled = true;
			try {
				const resp = await fetch("/api/profile/change-password", {
					method: "POST",
					headers: {
						"accept": "application/json",
						"content-type": "application/json",
					},
					body: JSON.stringify({
						password: pwd,
						confirm_password: confirm,
					}),
				});
				const json = await resp.json().catch(() => null);
				const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;
				if (!ok) {
					const msg = (json && json.message) || resp.statusText || "Update failed.";
					if (passwordMessage) passwordMessage.textContent = msg;
					passwordSubmit.disabled = false;
					return;
				}
				if (passwordMessage) passwordMessage.textContent = "Password updated.";
				setTimeout(() => closePasswordPanel(), 600);
			} catch (err) {
				if (passwordMessage) passwordMessage.textContent = String(err);
				passwordSubmit.disabled = false;
			}
		});
	}

	const deletePanel = document.querySelector("[data-delete-panel]");
	if (!deletePanel) return;
	const deleteOpen = document.querySelector("[data-delete-panel-toggle]");
	const deleteCloseEls = deletePanel.querySelectorAll("[data-delete-panel-close]");
	const deletePassword = deletePanel.querySelector("[data-delete-password]");
	const deleteSubmit = deletePanel.querySelector("[data-delete-submit]");
	const deleteMessage = deletePanel.querySelector("[data-delete-message]");

	function openDeletePanel() {
		if (deleteMessage) deleteMessage.textContent = "";
		if (deletePassword) deletePassword.value = "";
		deletePanel.hidden = false;
		requestAnimationFrame(() => deletePanel.classList.add("is-open"));
		if (deletePassword) deletePassword.focus();
	}

	function closeDeletePanel() {
		deletePanel.classList.remove("is-open");
		setTimeout(() => {
			deletePanel.hidden = true;
		}, ANIM_MS);
	}

	if (deleteOpen) {
		deleteOpen.addEventListener("click", (e) => {
			e.preventDefault();
			if (deletePanel.hidden) {
				if (passwordPanel && !passwordPanel.hidden) {
					holdCardHeight();
					closePasswordPanel();
					setTimeout(() => {
						openDeletePanel();
						releaseCardHeight(ANIM_MS);
					}, ANIM_MS);
				} else {
					openDeletePanel();
				}
				return;
			}
			closeDeletePanel();
		});
	}

	deleteCloseEls.forEach((btn) => {
		btn.addEventListener("click", (e) => {
			e.preventDefault();
			closeDeletePanel();
		});
	});

	if (deleteSubmit) {
		deleteSubmit.addEventListener("click", async (e) => {
			e.preventDefault();
			if (deleteMessage) deleteMessage.textContent = "";
			const pwd = deletePassword ? deletePassword.value : "";
			deleteSubmit.disabled = true;
			try {
				const resp = await fetch("/delete-account", {
					method: "POST",
					headers: {
						"accept": "application/json",
						"content-type": "application/json",
					},
					body: JSON.stringify({
						password: pwd,
					}),
				});
				const json = await resp.json().catch(() => null);
				const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;
				if (!ok) {
					const msg = (json && json.message) || resp.statusText || "Delete failed.";
					if (deleteMessage) deleteMessage.textContent = msg;
					deleteSubmit.disabled = false;
					return;
				}
				if (deleteMessage) deleteMessage.textContent = "Account deleted.";
				setTimeout(() => closeDeletePanel(), 600);
			} catch (err) {
				if (deleteMessage) deleteMessage.textContent = String(err);
				deleteSubmit.disabled = false;
			}
		});
	}

})();
