document.addEventListener("DOMContentLoaded", () => {
  const actionButtons = document.querySelectorAll("[data-user-action]");
  const badgeFor = (card) => card?.querySelector(".admin-user-badge");
  const nameFor = (card) => card?.querySelector(".admin-user-card__name")?.textContent || "User";

  async function handleUserAction(button) {
    const action = button.dataset.userAction;
    const userId = button.dataset.userId;
    if (!action || !userId) return;
    const card = button.closest("[data-user-card]");
    const label = nameFor(card);
    if (action === "delete") {
      openDeleteModal(button, label, userId);
      return;
    }
    const route = action === "promote"
      ? "/api/admin/users/promote"
      : "/api/admin/users/demote";
    button.disabled = true;
    try {
      const resp = await fetch(route, {
        method: "POST",
        headers: {
          "accept": "application/json",
          "content-type": "application/json",
        },
        body: JSON.stringify({ user_id: userId }),
      });
      const json = await resp.json().catch(() => null);
      const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;
      if (!ok) {
        window.alert((json && json.message) || "Action failed.");
        button.disabled = false;
        return;
      }
      const badge = badgeFor(card);
      if (badge) {
        if (action === "promote") {
          badge.textContent = "ADMIN";
          badge.classList.add("admin-user-badge--admin");
        } else {
          badge.textContent = "MEMBER";
          badge.classList.remove("admin-user-badge--admin");
        }
      }
      const promoteBtn = card?.querySelector("[data-user-action=\"promote\"]");
      const demoteBtn = card?.querySelector("[data-user-action=\"demote\"]");
      if (action === "promote") {
        if (promoteBtn) promoteBtn.disabled = true;
        if (demoteBtn) demoteBtn.disabled = false;
      } else {
        if (promoteBtn) promoteBtn.disabled = false;
        if (demoteBtn) demoteBtn.disabled = true;
      }
    } catch (err) {
      window.alert(String(err));
      button.disabled = false;
    }
  }

  actionButtons.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      handleUserAction(btn);
    });
  });

  const userDeleteModal = document.querySelector("[data-admin-user-delete-modal]");
  const userDeleteName = userDeleteModal?.querySelector("[data-admin-user-delete-name]");
  const userDeleteMessage = userDeleteModal?.querySelector("[data-admin-user-delete-message]");
  const userDeleteReason = userDeleteModal?.querySelector("[data-admin-user-delete-reason]");
  const userDeleteConfirm = userDeleteModal?.querySelector("[data-admin-user-delete-confirm]");
  const userDeleteSubmit = userDeleteModal?.querySelector("[data-admin-user-delete-submit]");
  const userDeleteCloseEls = userDeleteModal?.querySelectorAll("[data-admin-user-delete-close]") || [];
  let activeDeleteUserId = null;

  function openDeleteModal(trigger, label, userId) {
    if (!userDeleteModal) return;
    activeDeleteUserId = userId;
    if (userDeleteName) userDeleteName.textContent = label || "this user";
    if (userDeleteMessage) userDeleteMessage.textContent = "";
    if (userDeleteReason) userDeleteReason.value = "";
    if (userDeleteConfirm) userDeleteConfirm.checked = false;
    userDeleteModal.hidden = false;
    document.body.classList.add("modal-open");
  }

  function closeDeleteModal() {
    if (!userDeleteModal) return;
    userDeleteModal.hidden = true;
    document.body.classList.remove("modal-open");
    activeDeleteUserId = null;
  }

  userDeleteCloseEls.forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      closeDeleteModal();
    });
  });

  if (userDeleteSubmit) {
    userDeleteSubmit.addEventListener("click", async (event) => {
      event.preventDefault();
      if (!activeDeleteUserId) return;
      const reason = userDeleteReason ? userDeleteReason.value : "";
      const confirm = userDeleteConfirm ? userDeleteConfirm.checked : false;
      if (!confirm) {
        if (userDeleteMessage) userDeleteMessage.textContent = "Please confirm deletion.";
        return;
      }
      if (!reason) {
        if (userDeleteMessage) userDeleteMessage.textContent = "Please select a reason.";
        return;
      }
      userDeleteSubmit.disabled = true;
      try {
        const resp = await fetch("/api/admin/users/delete", {
          method: "POST",
          headers: {
            "accept": "application/json",
            "content-type": "application/json",
          },
          body: JSON.stringify({
            user_id: activeDeleteUserId,
            reason,
            confirm,
          }),
        });
        const json = await resp.json().catch(() => null);
        const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;
        if (!ok) {
          const msg = (json && json.message) || resp.statusText || "Delete failed.";
          if (userDeleteMessage) userDeleteMessage.textContent = msg;
          userDeleteSubmit.disabled = false;
          return;
        }
        const card = document.querySelector(`[data-user-card][data-user-id="${activeDeleteUserId}"]`);
        if (card) card.remove();
        closeDeleteModal();
      } catch (err) {
        if (userDeleteMessage) userDeleteMessage.textContent = String(err);
        userDeleteSubmit.disabled = false;
      }
    });
  }

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

  document.addEventListener("click", async (event) => {
      const enableBtn = event.target.closest("[data-integration-enable]");
      if (!enableBtn) return;
      event.preventDefault();
    enableBtn.disabled = true;
    try {
      const resp = await fetch(enableBtn.dataset.submitRoute || "/api/admin/users/integration/enable", {
        method: "POST",
        headers: {
          "accept": "application/json",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          integration_type: enableBtn.dataset.integrationType || "",
          integration_id: enableBtn.dataset.integrationId || "",
          user_id: enableBtn.dataset.userId || "",
        }),
      });
      const json = await resp.json().catch(() => null);
      const ok = json && typeof json.ok === "boolean" ? json.ok : resp.ok;
      if (!ok) {
        window.alert((json && json.message) || resp.statusText || "Enable failed.");
        enableBtn.disabled = false;
        return;
      }
      const card = enableBtn.closest("[data-integration-card]");
      if (card) {
        const badge = card.querySelector(".integration-badge");
        if (badge) {
          badge.textContent = enableBtn.dataset.activeLabel || "Active";
          badge.classList.remove("integration-badge--inactive");
        }
        const delBtn = card.querySelector("[data-integration-delete]");
        if (delBtn) delBtn.hidden = false;
      }
      enableBtn.remove();
    } catch (err) {
      window.alert(String(err));
      enableBtn.disabled = false;
    }
  });

  if (submitBtn) {
    submitBtn.addEventListener("click", async (event) => {
      event.preventDefault();
      if (!activeIntegration) return;
      const reason = reasonSelect ? reasonSelect.value : "";
      const confirm = confirmCheckbox ? confirmCheckbox.checked : false;
      const route = activeIntegration.dataset.submitRoute || "/api/admin/users/integration/disable";
      submitBtn.disabled = true;
      if (modalMessage) modalMessage.textContent = "";

      try {
        const resp = await fetch(route, {
          method: "POST",
          headers: {
            "accept": "application/json",
            "content-type": "application/json",
          },
          body: JSON.stringify({
            integration_type: activeIntegration.dataset.integrationType || "",
            integration_id: activeIntegration.dataset.integrationId || "",
            user_id: activeIntegration.dataset.userId || "",
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
          if (delBtn) delBtn.hidden = true;
          if (!card.querySelector("[data-integration-enable]")) {
            const enableBtn = document.createElement("button");
            enableBtn.className = "integration-enable";
            enableBtn.dataset.integrationEnable = "1";
            enableBtn.dataset.integrationType = activeIntegration.dataset.integrationType || "";
            enableBtn.dataset.integrationId = activeIntegration.dataset.integrationId || "";
            enableBtn.dataset.integrationLabel = activeIntegration.dataset.integrationLabel || "";
            enableBtn.dataset.integrationName = activeIntegration.dataset.integrationName || "";
            enableBtn.dataset.userId = activeIntegration.dataset.userId || "";
            enableBtn.dataset.submitRoute = "/api/admin/users/integration/enable";
            enableBtn.dataset.activeLabel = activeIntegration.dataset.activeLabel || (badge ? badge.textContent || "Active" : "Active");
            enableBtn.textContent = "Enable";
            delBtn?.insertAdjacentElement("beforebegin", enableBtn);
          }
        }
        closeModal();
      } catch (err) {
        if (modalMessage) modalMessage.textContent = String(err);
        submitBtn.disabled = false;
      }
    });
  }
});
