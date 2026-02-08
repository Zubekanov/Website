from __future__ import annotations

import html
from util.webpage_builder.parent_builder import HTMLHelper


def box_open(container_class: str, class_name: str) -> str:
	return f'<div class="{container_class}">\n\t<div class="{class_name}">\n'


def box_close() -> str:
	return "\t</div>\n</div>\n"


def paragraph(text: str) -> str:
	return f"<p>{text}</p>\n"


def error_header(code: int, description: str) -> str:
	return f"<h1>Error {code}</h1><p>{description}</p>\n"


def heading(text: str, level: int) -> str:
	return f"<h{level}>{text}</h{level}>\n"


def link_paragraph(link_html: str) -> str:
	return f"<p class=\"link-row\">{link_html}</p>\n"


def form_open(form_id: str = "", class_name: str = "form") -> str:
	id_attr = f' id="{form_id}"' if form_id else ""
	class_attr = f' class="{class_name}"' if class_name else ""
	return f"<form{id_attr}{class_attr}>\n"


def form_close() -> str:
	return "</form>\n"


def form_message_area(class_name: str, attr: str, lines: int) -> str:
	return f'<div class="{class_name}" {attr} data-lines="{lines}" aria-live="polite"></div>\n'

def api_scope_selector_input(
	options: list[tuple[str, str]],
	*,
	hidden_name: str = "requested_scopes",
	select_id: str = "requested_scopes_selector",
) -> str:
	options_html = "\n".join(
		f'<option value="{html.escape(value)}">{html.escape(label)}</option>'
		for value, label in options
	)
	return (
		f'<label for="{html.escape(select_id)}">Requested Scopes</label>'
		f'<div class="scope-selector" data-scope-selector data-target-input="{html.escape(hidden_name)}">'
		f'<input type="hidden" id="{html.escape(hidden_name)}" name="{html.escape(hidden_name)}" value="">'
		'<div class="scope-selector__selected" data-scope-selected>'
		'<span class="scope-selector__empty" data-scope-empty>No scopes selected yet.</span>'
		'</div>'
		f'<select id="{html.escape(select_id)}" class="scope-selector__dropdown" data-scope-dropdown>'
		'<option value="" selected>Select an option</option>'
		f"{options_html}"
		"</select>"
		"</div>"
	)


def centering_open(
	class_name: str,
	max_width: str,
	padding_y: str,
	padding_x: str,
) -> str:
	return (
		f'<div class="{class_name}" '
		f'style="max-width:{max_width}; padding:{padding_y} {padding_x}; margin:0 auto;">\n'
	)


def centering_close() -> str:
	return "</div>\n"


def centered_box_open(
	class_name: str,
	rounding: str,
	padding_y: str,
	padding_x: str,
	href: str | None = None,
) -> str:
	style = f'style="border-radius:{rounding}; padding:{padding_y} {padding_x};"'
	if href:
		return f'<a class="{class_name} {class_name}--link" href="{href}" {style}>\n'
	return f'<div class="{class_name}" {style}>\n'


def centered_box_close(is_link: bool) -> str:
	return "</a>\n" if is_link else "</div>\n"


def return_home() -> str:
	return "<p><a href='/'>Return to Home Page</a></p>\n"


def subscription_card(
	event_key: str,
	permission: str,
	description: str,
	date_str: str,
	status_label: str,
	is_active: bool,
	unsubscribe_html: str,
	resubscribe_html: str,
) -> str:
	status_class = " subscription-status--inactive" if not is_active else ""
	return (
		"<div class=\"subscription-card\" data-subscription-card>"
		"<div class=\"subscription-main\">"
		f"<div class=\"subscription-title\">{html.escape(event_key)}</div>"
		f"<div class=\"subscription-permission\">{html.escape(permission)}</div>"
		f"<div class=\"subscription-desc\">{html.escape(description)}</div>"
		"</div>"
		"<div class=\"subscription-footer\">"
		f"<span class=\"subscription-date\">Subscribed {html.escape(date_str) if date_str else ''}</span>"
		f"<span class=\"subscription-status{status_class}\">{status_label}</span>"
		f"{unsubscribe_html}"
		f"{resubscribe_html}"
		"</div>"
		"</div>"
	)


def subscription_action(action: str, subscription_id: str, route: str, label: str) -> str:
	return HTMLHelper.button(
		label,
		size="sm",
		shape="pill",
		data_attrs={
			"subscription-action": action,
			"subscription-id": subscription_id,
			"submit-route": route,
		},
	)


def integration_subscriptions(title: str, inner_html: str) -> str:
	return (
		"<div class=\"integration-subscriptions\">"
		f"<div class=\"integration-subtitle\">{title}</div>"
		f"{inner_html}"
		"</div>"
	)


def integration_subscriptions_empty(title: str) -> str:
	return (
		"<div class=\"integration-subscriptions\">"
		f"<div class=\"integration-subtitle\">{title}</div>"
		"<div class=\"subscription-empty\">No subscriptions yet.</div>"
		"</div>"
	)


def integration_delete_button(
	integration_type: str,
	integration_id: str,
	label: str,
	status: str,
	is_active: bool,
) -> str:
	return integration_delete_action(integration_type, integration_id, label, is_active) + integration_badge(status)


def integration_delete_action(
	integration_type: str,
	integration_id: str,
	label: str,
	is_active: bool,
	*,
	user_id: str | None = None,
	submit_route: str | None = None,
	hidden: bool = False,
	active_label: str | None = None,
) -> str:
	data_attrs = {
		"integration-delete": "1",
		"integration-type": integration_type,
		"integration-id": integration_id,
		"integration-name": label,
		"integration-label": label,
	}
	if not is_active:
		data_attrs["integration-inactive"] = "1"
	if user_id:
		data_attrs["user-id"] = user_id
	if submit_route:
		data_attrs["submit-route"] = submit_route
	if active_label:
		data_attrs["active-label"] = active_label

	attrs = {}
	if hidden:
		attrs["hidden"] = "hidden"

	return HTMLHelper.button(
		"Disable",
		size="sm",
		shape="pill",
		class_name="btn--integration-delete",
		data_attrs=data_attrs,
		attrs=attrs,
	)


def integration_enable_action(
	integration_type: str,
	integration_id: str,
	label: str,
	active_label: str,
	*,
	user_id: str | None = None,
	submit_route: str | None = None,
) -> str:
	user_attr = f" data-user-id=\"{html.escape(user_id)}\"" if user_id else ""
	route_attr = f" data-submit-route=\"{html.escape(submit_route)}\"" if submit_route else ""
	return (
		f"<button class=\"integration-enable\" data-integration-enable "
		f"data-integration-type=\"{html.escape(integration_type)}\" "
		f"data-integration-id=\"{html.escape(integration_id)}\" "
		f"data-integration-name=\"{html.escape(label)}\" "
		f"data-integration-label=\"{html.escape(label)}\" "
		f"data-active-label=\"{html.escape(active_label)}\"{user_attr}{route_attr}>Enable</button>"
	)


def admin_users_shell(contents: str) -> str:
	return (
		"<div class=\"admin-users\">"
		"<header class=\"admin-users__header\">"
		"<div>"
		"<h1>User Management</h1>"
		"<p>Review accounts, roles, and linked integrations.</p>"
		"</div>"
		"</header>"
		f"<section class=\"admin-users__list\">{contents}</section>"
		"</div>"
	)


def admin_user_badge(label: str) -> str:
	label_safe = html.escape(label)
	cls = " admin-user-badge--admin" if label.upper() == "ADMIN" else ""
	return f"<span class=\"admin-user-badge{cls}\">{label_safe}</span>"


def admin_user_actions(actions_html: str) -> str:
	return f"<div class=\"admin-user-actions\">{actions_html}</div>"


def admin_user_action_button(action: str, user_id: str, label: str, is_danger: bool = False) -> str:
	action_safe = html.escape(action)
	user_safe = html.escape(user_id)
	label_safe = html.escape(label)
	class_name = "admin-user-action admin-user-action--danger" if is_danger else "admin-user-action"
	return (
		f"<button class=\"{class_name}\" data-user-action=\"{action_safe}\" "
		f"data-user-id=\"{user_safe}\">{label_safe}</button>"
	)


def admin_user_card(
	user_id: str,
	name: str,
	email: str,
	meta_html: str,
	badge_html: str,
	actions_html: str,
	integrations_html: str,
) -> str:
	return (
		f"<article class=\"admin-user-card\" data-user-card data-user-id=\"{html.escape(user_id)}\">"
		"<div class=\"admin-user-card__header\">"
		"<div>"
		f"<div class=\"admin-user-card__name\">{html.escape(name)}</div>"
		f"<div class=\"admin-user-card__email\">{html.escape(email)}</div>"
		f"<div class=\"admin-user-card__meta\">{meta_html}</div>"
		"</div>"
		f"{badge_html}"
		"</div>"
		f"{actions_html}"
		"<div class=\"admin-user-card__integrations\">"
		f"{integrations_html}"
		"</div>"
		"</article>"
	)


def admin_user_meta_row(label: str, value: str) -> str:
	return (
		"<div class=\"admin-user-meta-row\">"
		f"<span class=\"admin-user-meta-label\">{html.escape(label)}</span>"
		f"<span class=\"admin-user-meta-value\">{html.escape(value)}</span>"
		"</div>"
	)


def admin_user_integrations(cards_html: str) -> str:
	return f"<div class=\"integration-grid\">{cards_html}</div>"


def admin_user_delete_reason_select(options: list[tuple[str, str]], selected: str = "") -> str:
	option_html = []
	for value, label in options:
		selected_attr = " selected" if value == selected else ""
		option_html.append(f"<option value=\"{html.escape(value)}\"{selected_attr}>{html.escape(label)}</option>")
	return (
		"<select id=\"admin-user-delete-reason\" data-admin-user-delete-reason>"
		f"{''.join(option_html)}"
		"</select>"
	)


def admin_user_delete_modal(reasons_html: str) -> str:
	return (
		"<div class=\"integration-delete-modal\" data-admin-user-delete-modal hidden>"
		"<div class=\"integration-delete-modal__backdrop\" data-admin-user-delete-close></div>"
		"<div class=\"integration-delete-modal__card\">"
		"<div class=\"integration-delete-modal__header\">"
		"<h3>Delete user account</h3>"
		"<button class=\"integration-delete-modal__close\" data-admin-user-delete-close>×</button>"
		"</div>"
		"<p class=\"integration-delete-modal__text\">"
		"You're about to disable <span data-admin-user-delete-name></span> and remove their integrations."
		"</p>"
		"<div class=\"integration-delete-modal__fields\">"
		"<label for=\"admin-user-delete-reason\">Reason for deletion</label>"
		f"{reasons_html}"
		"<label class=\"integration-delete-modal__confirm\">"
		"<input type=\"checkbox\" data-admin-user-delete-confirm>"
		"<span>I understand this disables the account and revokes sessions.</span>"
		"</label>"
		"</div>"
		"<div class=\"integration-delete-modal__actions\">"
		"<button class=\"integration-delete-modal__cancel\" data-admin-user-delete-close>Cancel</button>"
		"<button class=\"integration-delete-modal__submit\" data-admin-user-delete-submit>Delete</button>"
		"</div>"
		"<div class=\"integration-delete-modal__message\" data-admin-user-delete-message></div>"
		"</div>"
		"</div>"
	)


def integration_badge(status: str) -> str:
	status_class = " integration-badge--inactive" if status == "Suspended" else ""
	return f"<span class=\"integration-badge{status_class}\">{status}</span>"


def integration_card(
	card_type: str,
	card_id: str,
	title: str,
	meta: str,
	subtitle: str,
	badge_html: str,
	subscriptions_html: str = "",
) -> str:
	return (
		f"<div class=\"integration-card\" data-integration-card=\"{html.escape(card_type)}\" "
		f"data-integration-id=\"{html.escape(card_id)}\">"
		f"<div class=\"integration-title\">{title}</div>"
		f"<div class=\"integration-meta\">{meta}</div>"
		f"<div class=\"integration-sub\">{subtitle}</div>"
		f"{badge_html}"
		f"{subscriptions_html}"
		"</div>"
	)


def secret_field(value: str, *, label: str = "Secret", mask: str | None = None) -> str:
	escaped = html.escape(value or "")
	if not escaped:
		return "<span class=\"secret-empty\">(empty)</span>"
	mask_text = mask or f"{label} hidden"
	return (
		"<div class=\"secret-field\" data-secret>"
		f"<span class=\"secret-value\" data-secret-mask>{html.escape(mask_text)}</span>"
		f"<span class=\"secret-value secret-value--real\" data-secret-reveal>{escaped}</span>"
		"<button class=\"secret-copy\" type=\"button\" data-secret-copy "
		"aria-label=\"Copy secret\" title=\"Copy\">"
		"<img src=\"/static/img/copy.png\" alt=\"\">"
		"<span class=\"secret-tooltip\" data-secret-tooltip aria-hidden=\"true\">Copied</span>"
		"</button>"
		"<button class=\"secret-toggle\" type=\"button\" data-secret-toggle "
		"aria-label=\"Reveal secret\">"
		"<img src=\"/static/img/hidden.png\" alt=\"Hidden\" data-secret-icon-hidden>"
		"<img src=\"/static/img/eye.png\" alt=\"Visible\" data-secret-icon-visible>"
		"</button>"
		"</div>"
	)


def integration_card_empty(message: str | None = None) -> str:
	title = "No linked integrations"
	subtext = message or "You have not connected any services yet."
	return (
		"<div class=\"integration-card integration-card--empty\">"
		f"<div class=\"integration-title\">{html.escape(title)}</div>"
		f"<div class=\"integration-sub\">{html.escape(subtext)}</div>"
		"</div>"
	)


def profile_page_shell(contents: str) -> str:
	return f"<div class=\"profile-page\">{contents}</div>"


def profile_card(
	initials: str,
	user_name: str,
	created_at: str,
	email: str,
	badge_label: str,
	admin_line: str,
	panels_html: str = "",
) -> str:
	return (
		"<section class=\"profile-card\">"
		"<div class=\"profile-card__core\">"
		"<div class=\"profile-header\">"
		f"<div class=\"profile-avatar\">{html.escape(initials)}</div>"
		"<div class=\"profile-meta\">"
		f"<h2>{html.escape(user_name)}</h2>"
		f"<p class=\"profile-sub\">Member since {html.escape(created_at)}</p>"
		f"{admin_line}"
		f"<p class=\"profile-email\"><span>Email</span>{html.escape(email)}</p>"
		"</div>"
		f"{profile_badge(badge_label)}"
		"</div>"
		"<div class=\"profile-actions\">"
		"<button class=\"profile-action\" data-password-panel-toggle>Change Password</button>"
		"<button class=\"profile-action profile-action--danger\" data-delete-panel-toggle>Delete Account</button>"
		"</div>"
		"</div>"
		f"{panels_html}"
		"</section>"
	)


def profile_badge(label: str, *, static: bool = False) -> str:
	is_admin = (label or "").upper() != "MEMBER"
	static_class = " profile-badge--static" if static else ""
	return (
		f"<span class=\"profile-badge{' profile-badge--admin' if is_admin else ''}{static_class}\">"
		f"{html.escape(label)}"
		"</span>"
	)


def profile_admin_line(date_str: str) -> str:
	return f"<p class=\"profile-sub\">Admin since {html.escape(date_str)}</p>"


def profile_password_panel() -> str:
	return (
		"<div class=\"profile-password-panel form\" data-password-panel hidden>"
		"<div class=\"profile-password-panel__header\">"
		"<h3>Change Password</h3>"
		"<button class=\"profile-password-panel__close\" data-password-panel-close>×</button>"
		"</div>"
		"<div class=\"profile-password-panel__fields\">"
		"<div class=\"form-group\">"
		"<label for=\"profile-password\">New password</label>"
		"<input type=\"password\" id=\"profile-password\" data-password-input>"
		"</div>"
		"<div class=\"form-group\">"
		"<label for=\"profile-password-confirm\">Confirm password</label>"
		"<input type=\"password\" id=\"profile-password-confirm\" data-password-confirm>"
		"</div>"
		"</div>"
		"<div class=\"profile-password-panel__actions\">"
		"<button class=\"profile-password-panel__cancel\" data-password-panel-close>Cancel</button>"
		"<button class=\"profile-password-panel__submit\" data-password-submit>Update Password</button>"
		"</div>"
		"<div class=\"profile-password-panel__message\" data-password-message></div>"
		"</div>"
	)


def profile_delete_panel() -> str:
	return (
		"<div class=\"profile-delete-panel form\" data-delete-panel hidden>"
		"<div class=\"profile-delete-panel__header\">"
		"<h3>Delete Account</h3>"
		"<button class=\"profile-delete-panel__close\" data-delete-panel-close>×</button>"
		"</div>"
		"<p class=\"profile-delete-panel__text\">Deleting your account is irreversible.</p>"
		"<div class=\"profile-delete-panel__fields\">"
		"<div class=\"form-group\">"
		"<label for=\"profile-delete-password\">Confirm password</label>"
		"<input type=\"password\" id=\"profile-delete-password\" data-delete-password>"
		"</div>"
		"</div>"
		"<div class=\"profile-delete-panel__actions\">"
		"<button class=\"profile-delete-panel__cancel\" data-delete-panel-close>Cancel</button>"
		"<button class=\"profile-delete-panel__submit\" data-delete-submit>Delete Account</button>"
		"</div>"
		"<div class=\"profile-delete-panel__message\" data-delete-message></div>"
		"</div>"
	)


def profile_integrations_header(title: str, subtitle: str, cards_html: str) -> str:
	return (
		"<section class=\"profile-integrations\">"
		"<div class=\"profile-section-header\">"
		f"<h2>{title}</h2>"
		f"<p>{subtitle}</p>"
		"</div>"
		f"<div class=\"integration-grid\">{cards_html}</div>"
		"</section>"
	)

def profile_popugame_history_card(
	*,
	elo: int,
	total_wr: float,
	wins: int,
	losses: int,
	draws: int,
	boxes: list[dict[str, str]],
) -> str:
	ordered_boxes = list(reversed(boxes))
	boxes_html = "".join(
		f"<span class=\"profile-popu-box profile-popu-box--{html.escape(b.get('outcome', 'draw'))}\" "
		f"data-played=\"1\" "
		f"data-outcome=\"{html.escape(b.get('outcome', 'draw'))}\" "
		f"data-tooltip=\"{html.escape(b.get('tooltip', ''))}\" "
		f"aria-label=\"{html.escape(b.get('tooltip', ''))}\"></span>"
		for b in ordered_boxes
	)
	return (
		"<section class=\"profile-popugame\">"
		"<div class=\"profile-section-header\">"
		"<h2>PopuGame History</h2>"
		"<p>Recent games and rating trend.</p>"
		"</div>"
		"<div class=\"profile-popugame__stats\">"
		f"<div class=\"profile-popu-stat\"><span>ELO</span><strong>{elo}</strong></div>"
		f"<div class=\"profile-popu-stat\"><span>Total WR</span><strong>{total_wr:.1f}%</strong></div>"
		f"<div class=\"profile-popu-stat\"><span>Record</span><strong>{wins}-{losses}-{draws}</strong></div>"
		"</div>"
		"<div class=\"profile-popugame__history-box\">"
		f"<div class=\"profile-popugame__history\" data-popu-history>{boxes_html}</div>"
		"</div>"
		"</section>"
	)


def integration_delete_modal(reasons_html: str) -> str:
	return (
		"<div class=\"integration-delete-modal\" data-integration-modal hidden>"
		"<div class=\"integration-delete-modal__backdrop\" data-integration-modal-close></div>"
		"<div class=\"integration-delete-modal__card\">"
		"<div class=\"integration-delete-modal__header\">"
		"<h3>Disable integration</h3>"
		"<button class=\"integration-delete-modal__close\" data-integration-modal-close>×</button>"
		"</div>"
		"<p class=\"integration-delete-modal__text\">You're about to disable <span data-integration-modal-name></span>.</p>"
		"<div class=\"integration-delete-modal__fields\">"
		"<label for=\"integration-delete-reason\">Reason for disabling</label>"
		f"{reasons_html}"
		"</div>"
		"<label class=\"integration-delete-modal__confirm\">"
		"<input type=\"checkbox\" data-integration-confirm>"
		"<span>Confirm disable</span>"
		"</label>"
		"<div class=\"integration-delete-modal__actions\">"
		"<button class=\"integration-delete-modal__cancel\" data-integration-modal-close>Cancel</button>"
		"<button class=\"integration-delete-modal__submit\" data-integration-submit>Delete</button>"
		"</div>"
		"<div class=\"integration-delete-modal__message\" data-integration-modal-message></div>"
		"</div>"
		"</div>"
	)


def integration_delete_reason_select(options: list[tuple[str, str]], selected: str = "") -> str:
	option_html = []
	for value, label in options:
		selected_attr = " selected" if value == selected else ""
		option_html.append(f"<option value=\"{html.escape(value)}\"{selected_attr}>{html.escape(label)}</option>")
	return (
		"<select id=\"integration-delete-reason\" data-integration-reason>"
		f"{''.join(option_html)}"
		"</select>"
	)


def admin_dashboard(cards_html: str) -> str:
	return (
		"<div class=\"admin-dashboard\">"
		"<header class=\"admin-dashboard__header\">"
		"<div>"
		"<h1>Admin Dashboard</h1>"
		"<p>Manage approvals, data, and system tooling.</p>"
		"</div>"
		"</header>"
		f"<section class=\"admin-dashboard__grid\">{cards_html}</section>"
		"</div>"
	)


def admin_card(
	href: str,
	meta_html: str,
	title: str,
	description: str,
) -> str:
	return (
		f"<a class=\"admin-card\" href=\"{href}\">"
		f"{meta_html}"
		f"<div class=\"admin-card__title\">{title}</div>"
		f"<div class=\"admin-card__desc\">{description}</div>"
		"</a>"
	)


def admin_card_meta(text: str, badge_html: str = "") -> str:
	return f"<div class=\"admin-card__meta\">{text}{badge_html}</div>"


def admin_badge_count(count: int | None) -> str:
	if count is None:
		return "<span class=\"admin-badge admin-badge--loading\"></span>"
	label = "99+" if count > 99 else str(count)
	alert_class = " is-alert" if count > 0 else ""
	return f"<span class=\"admin-badge{alert_class}\">{label}</span>"


def approval_row(label: str, value: str, full: bool = False) -> str:
	cls = "approval-card__row full" if full else "approval-card__row"
	return f"<div class=\"{cls}\"><span class=\"label\">{label}</span><span class=\"value\">{value}</span></div>"


def approval_card(title: str, subtitle: str, status_label: str, rows_html: str, actions_html: str) -> str:
	return (
		"<div class=\"centered-box approval-card\" data-approval-card>"
		"<div class=\"approval-card__header\">"
		f"<div><div class=\"approval-card__title\">{title}</div>"
		f"<div class=\"approval-card__subtitle\">{subtitle}</div></div>"
		f"<div class=\"approval-card__status\">{status_label}</div>"
		"</div>"
		"<div class=\"approval-card__grid\">"
		f"{rows_html}"
		"</div>"
		f"{actions_html}"
		"</div>"
	)


def approval_actions(approve_route: str, deny_route: str, request_id: str) -> str:
	req_id = html.escape(str(request_id))
	return (
		"<div class=\"approval-card__actions\">"
		f"<button data-approval-action=\"approve\" data-submit-route=\"{approve_route}\" "
		f"data-submit-method=\"POST\" data-request-id=\"{req_id}\">Approve</button>"
		f"<button class=\"danger\" data-approval-action=\"deny\" data-submit-route=\"{deny_route}\" "
		f"data-submit-method=\"POST\" data-request-id=\"{req_id}\">Deny</button>"
		"</div>"
	)


def add_button_class(button_html: str, class_name: str) -> str:
	if not class_name:
		return button_html
	return button_html.replace("<button ", f'<button class="{class_name}" ', 1)


def paragraph_with_strong(label: str, value: str) -> str:
	return f"<p><strong>{html.escape(label)}</strong> {value}</p>\n"


def paragraph_with_bold(prefix: str, bold_text: str, suffix: str = "") -> str:
	return (
		"<p>"
		f"{html.escape(prefix)}"
		f"<b>{html.escape(bold_text)}</b>"
		f"{html.escape(suffix)}"
		"</p>\n"
	)


def webhook_selector_input(
	label: str,
	input_id: str,
	name: str,
	placeholder: str,
	data_kind: str,
) -> str:
	return HTMLHelper.text_input(
		label=html.escape(label),
		name=name,
		input_id=input_id,
		placeholder=html.escape(placeholder),
		input_attrs={
			"data-webhook-input": data_kind,
			"autocomplete": "off",
		},
	)


def webhook_options_data_script(options_b64: str) -> str:
	return (
		'<script type="application/json" id="webhook-options-data" '
		f'data-encoding="base64">{options_b64}</script>'
	)


def inline_script(script_body: str) -> str:
	return f"<script>{script_body}</script>"


def webhook_verify_autosubmit_script() -> str:
	return inline_script(
		"(function(){"
		"var form=document.getElementById('discord-webhook-verify-form');"
		"if(!form) return;"
		"var code=document.querySelector('[name=\"verification_code\"]').value;"
		"var vid=document.querySelector('[name=\"verification_id\"]').value;"
		"if(code && vid){"
		"var btn=form.querySelector('button[data-submit-route]');"
		"if(btn) btn.click();"
		"}"
		"})();"
	)


def metrics_dashboard_open() -> str:
	return (
		"<div class=\"metrics-dashboard\">"
		"<header class=\"metrics-header\">"
		"<div>"
		"<h1>Server Metrics</h1>"
		"<p>Live telemetry from the server stack.</p>"
		"</div>"
		"<div class=\"metrics-controls\">"
		"<span class=\"metrics-status\" data-live-badge>LIVE</span>"
		"<div class=\"metrics-range\">"
		"<button data-range=\"1h\" class=\"range-btn\">1h</button>"
		"<button data-range=\"24h\" class=\"range-btn\">24h</button>"
		"<button data-range=\"7d\" class=\"range-btn\">7d</button>"
		"<button data-range=\"30d\" class=\"range-btn\">30d</button>"
		"<button data-range=\"1y\" class=\"range-btn\">1y</button>"
		"</div>"
		"</div>"
		"</header>"
		"<section class=\"metrics-kpis\">"
	)


def metrics_kpi_card(key: str, label: str) -> str:
	return (
		"<div class=\"kpi-card\" data-kpi=\"1\">"
		f"<div class=\"kpi-label\">{html.escape(label)}</div>"
		f"<div class=\"kpi-value\" data-metric-kpi=\"{html.escape(key)}\">--</div>"
		"</div>"
	)


def metrics_dashboard_between_sections() -> str:
	return "</section><section class=\"metrics-grid\">"


def metrics_dashboard_close() -> str:
	return "</section></div>"


def minecraft_status_card(host: str = "mc.zubekanov.com") -> str:
	host_safe = html.escape((host or "").strip() or "mc.zubekanov.com")
	return (
		"<div class=\"minecraft-status-card\" data-mc-status>"
		"<div class=\"minecraft-status-header\">"
		"<h3>Server Status</h3>"
		"<span class=\"minecraft-status-pill minecraft-status-pill--loading\" data-mc-status-pill>Checking...</span>"
		"</div>"
		"<div class=\"minecraft-status-body\">"
		"<div class=\"minecraft-status-row\"><span class=\"label\">Host</span>"
		"<span class=\"value\">"
		"<span class=\"minecraft-host-chip\" data-mc-host-chip>"
		f"<span class=\"minecraft-host-text\" data-mc-host>{host_safe}</span>"
		"<button class=\"minecraft-host-copy\" type=\"button\" data-mc-copy "
		"aria-label=\"Copy server address\" title=\"Copy\">"
		"<img src=\"/static/img/copy.png\" alt=\"\">"
		"</button>"
		"<span class=\"minecraft-host-tooltip\" data-mc-tooltip aria-hidden=\"true\">Copied</span>"
		"</span>"
		"</span></div>"
		"<div class=\"minecraft-status-row\"><span class=\"label\">MOTD</span>"
		"<span class=\"value\" data-mc-motd>Fetching description...</span></div>"
		"<div class=\"minecraft-status-row\"><span class=\"label\">Players</span>"
		"<span class=\"value\" data-mc-players>--</span></div>"
		"<div class=\"minecraft-status-player-list\" data-mc-player-list hidden></div>"
		"<div class=\"minecraft-status-row\"><span class=\"label\">Version</span>"
		"<span class=\"value\" data-mc-version>--</span></div>"
		"<div class=\"minecraft-status-row\"><span class=\"label\">Latency</span>"
		"<span class=\"value\" data-mc-latency>--</span></div>"
		"</div>"
		"<div class=\"minecraft-status-footer\" data-mc-status-note>Fetching status...</div>"
		"</div>\n"
	)


def minecraft_whitelist_banner(is_whitelisted: bool, username: str) -> str:
	whitelist_attr = ' data-is-whitelisted="true"' if is_whitelisted else ""
	label = html.escape(username) if username else "your account"
	return (
		f"<div class=\"minecraft-whitelist-banner\" data-mc-whitelist{whitelist_attr}>"
		"<div class=\"minecraft-whitelist-title\">You are already whitelisted</div>"
		"<div class=\"minecraft-whitelist-subtitle\">"
		f"Whitelisted under <b>{label}</b>."
		"</div>"
		f"{HTMLHelper.button('Make a new application', size='sm', shape='pill', variant='accent', data_attrs={'mc-toggle': ''})}"
		"</div>"
	)


def minecraft_registration_wrap_open(is_whitelisted: bool) -> str:
	hidden_class = " is-hidden" if is_whitelisted else ""
	return f"<div id=\"minecraft-registration-wrap\" class=\"minecraft-registration-wrap{hidden_class}\">"


def minecraft_registration_wrap_close() -> str:
	return "</div>"


def db_admin_open() -> str:
	return "<div class=\"db-admin\">"


def db_admin_close() -> str:
	return "</div>"


def db_admin_message() -> str:
	return "<div data-form-message hidden></div>"


def db_user_id_options_script(options_b64: str) -> str:
	return (
		'<script type="application/json" id="user-id-options-data" '
		f'data-encoding="base64">{options_b64}</script>'
	)


def db_section_open(title: str) -> str:
	return f"<div class=\"db-section\"><h2>{html.escape(title)}</h2>"


def db_section_no_pk() -> str:
	return "<p>Table has no primary key; editing disabled.</p></div>"


def db_section_close() -> str:
	return "</div>"


def db_grid_open(
	grid_cols: str,
	col_count: int,
	columns: list[str],
	col_types: str,
	pk_cols_attr: str,
	actions_width: int = 160,
) -> str:
	return (
		f'<div class="db-grid" style="grid-template-columns: {grid_cols};" '
		f'data-col-count="{col_count}" data-actions-width="{actions_width}" '
		f'data-columns="{html.escape(",".join(columns))}" '
		f'data-col-types="{html.escape(col_types)}" '
		f'data-pk-cols="{html.escape(pk_cols_attr)}">'
	)


def db_grid_close() -> str:
	return "</div>"


def db_grid_head_row(columns: list[str]) -> str:
	cells = []
	for i, col in enumerate(columns):
		cells.append(f'<div class="db-cell db-cell--head" data-col-index="{i}">{html.escape(col)}</div>')
	cells.append('<div class="db-cell db-cell--head db-cell--actions" data-actions-col="1">Actions</div>')
	return '<div class="db-grid-row db-grid-head">' + "".join(cells) + "</div>"


def db_grid_empty_row() -> str:
	return (
		'<div class="db-grid-row db-grid-empty">'
		'<div class="db-cell db-cell--empty">No rows found.</div>'
		"</div>"
	)


def db_row_form_open() -> str:
	return '<form class="db-grid-row db-row-form db-row-form--data form">'


def db_row_form_close() -> str:
	return "</form>"


def db_add_row_head() -> str:
	return (
		'<div class="db-grid-row db-add-row-head">'
		'<div class="db-cell db-cell--section db-cell--span-all">Add Row</div>'
		"</div>"
	)


def db_row_add_open() -> str:
	return '<form class="db-grid-row db-row-form db-row-form--add form">'


def db_row_add_close() -> str:
	return "</form>"


def db_enum_options(enum_vals: list[str], selected: str | None = None, include_blank: bool = True) -> str:
	options = []
	if include_blank:
		options.append('<option value=""></option>')
	for enum_val in enum_vals:
		selected_attr = " selected" if selected == enum_val else ""
		options.append(f'<option value="{html.escape(enum_val)}"{selected_attr}>{html.escape(enum_val)}</option>')
	return "".join(options)


def db_cell_enum(i: int, col: str, options_html: str) -> str:
	return (
		f'<div class="db-cell" data-col-index="{i}">'
		f'<select class="db-form-input" name="col__{html.escape(col)}">{options_html}</select>'
		"</div>"
	)


def db_cell_checkbox(i: int, col: str, checked: bool) -> str:
	checked_attr = " checked" if checked else ""
	return (
		f'<div class="db-cell db-cell--checkbox" data-col-index="{i}">'
		f'<input class="db-form-input db-form-input--checkbox" type="checkbox" name="col__{html.escape(col)}"{checked_attr}>'
		"</div>"
	)


def db_cell_text(
	i: int,
	col: str,
	val_str: str,
	max_len: int | None,
	user_id_input: bool,
	tooltip_attr: str,
	tooltip_class: str,
) -> str:
	user_id_attr = ' data-user-id-input="1"' if user_id_input else ""
	max_len_attr = f' maxlength="{int(max_len)}"' if max_len else ""
	return (
		f'<div class="db-cell{tooltip_class}" data-col-index="{i}"{tooltip_attr}>'
		f'<input class="db-form-input" type="text" name="col__{html.escape(col)}" '
		f'{user_id_attr} value="{html.escape(val_str)}"{max_len_attr}>'
		"</div>"
	)


def db_actions_cell(fields_attr: str) -> str:
	return (
		'<div class="db-cell db-cell--actions">'
		"<div class=\"db-actions\">"
		f"{HTMLHelper.button('Save', button_type='submit', size='xs', data_attrs={'db-action':'update','submit-route':'/api/admin/db/update-row','submit-method':'POST','submit-fields':fields_attr})}"
		f"{HTMLHelper.button('Delete', button_type='submit', size='xs', variant='danger', data_attrs={'db-action':'delete','submit-route':'/api/admin/db/delete-row','submit-method':'POST','submit-fields':fields_attr})}"
		"</div>"
		"</div>"
	)


def db_add_actions_cell(insert_fields_attr: str) -> str:
	return (
		'<div class="db-cell db-cell--actions">'
		f"{HTMLHelper.button('Add', button_type='submit', size='xs', data_attrs={'db-action':'add','submit-route':'/api/admin/db/insert-row','submit-method':'POST','submit-fields':insert_fields_attr})}"
		"</div>"
	)


def center_column(content_html: str) -> str:
	return (
		"<div class=\"center-column\">"
		"<div class=\"center-column__content\">"
		f"{content_html}"
		"</div>"
		"</div>"
	)

def integration_remove_form(token: str) -> str:
	escaped = html.escape(token or "")
	return (
		"<form class=\"form\" id=\"integration-remove-form\">"
		f"<input type=\"hidden\" name=\"token\" value=\"{escaped}\">"
		"<div class=\"form-group\">"
		"<p>This integration was created without a linked account. Confirm removal below.</p>"
		"</div>"
		"<button type=\"submit\" class=\"form-submit danger\" "
		"data-submit-route=\"/api/integration/remove\" data-submit-method=\"POST\" "
		"data-submit-fields=\"token\" data-success-redirect=\"/integration/removed\">"
		"Remove integration</button>"
		"</form>"
	)


def email_debug_form() -> str:
	return (
		"<h1>Debug Email</h1>"
		"<p>Send a test email using the configured Gmail integration.</p>"
		"<form class=\"form\" id=\"debug-email-form\">"
		"<div class=\"form-group\">"
		"<label for=\"debug-email-to\">Recipient Email</label>"
		"<input id=\"debug-email-to\" name=\"to_email\" type=\"email\" placeholder=\"you@example.com\" required>"
		"</div>"
		"<div class=\"form-group\">"
		"<label for=\"debug-email-verify\">"
		"<input id=\"debug-email-verify\" name=\"send_verify\" type=\"checkbox\">"
		"Send verification email"
		"</label>"
		"</div>"
		"<div class=\"form-group\" data-debug-toggle=\"verify\">"
		"<label for=\"debug-email-code\">Verification Code (optional)</label>"
		"<input id=\"debug-email-code\" name=\"verify_code\" type=\"text\" placeholder=\"dummy-code\">"
		"</div>"
		"<div class=\"form-group\" data-debug-toggle=\"custom\">"
		"<label for=\"debug-email-subject\">Subject</label>"
		"<input id=\"debug-email-subject\" name=\"subject\" type=\"text\" placeholder=\"Test subject\" required>"
		"</div>"
		"<div class=\"form-group\" data-debug-toggle=\"custom\">"
		"<label for=\"debug-email-body\">Body</label>"
		"<textarea id=\"debug-email-body\" name=\"body\" rows=\"6\" placeholder=\"Message body\" required></textarea>"
		"</div>"
		"<button type=\"submit\" class=\"primary\" "
		"data-submit-route=\"/api/admin/email/debug\" data-submit-method=\"POST\" "
		"data-submit-fields=\"to_email,send_verify,verify_code,subject,body\">Send Debug Email</button>"
		"<div class=\"form-message\" data-form-message hidden></div>"
		"</form>"
	)


def email_debug_script() -> str:
	return inline_script(
		"(function(){"
		"var checkbox=document.getElementById('debug-email-verify');"
		"var groups=document.querySelectorAll('[data-debug-toggle=\"custom\"]');"
		"var verifyGroups=document.querySelectorAll('[data-debug-toggle=\"verify\"]');"
		"function sync(){"
		"var hide=checkbox && checkbox.checked;"
		"groups.forEach(function(g){"
		"g.style.display=hide?'none':'';"
		"g.setAttribute('aria-hidden', hide ? 'true' : 'false');"
		"});"
		"verifyGroups.forEach(function(g){"
		"g.style.display=hide?'':'none';"
		"g.setAttribute('aria-hidden', hide ? 'false' : 'true');"
		"});"
		"}"
		"if(checkbox){checkbox.addEventListener('change', sync); sync();}"
		"})();"
	)
