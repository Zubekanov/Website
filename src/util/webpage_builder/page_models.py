from __future__ import annotations

import base64
import html
import json
import os
from pathlib import Path
from typing import Any

from util.integrations.discord.webhook_interface import DiscordWebhookEmitter
from util.navbars.visibility import filter_nav_items
from util.webpage_builder import html_fragments, parent_builder


def _hidden_input(name: str, value: str) -> str:
	return f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value)}">\n'


def load_readme_text() -> str:
	readme_path = Path(__file__).resolve().parents[3] / "README.md"
	try:
		return readme_path.read_text(encoding="utf-8", errors="replace")
	except Exception:
		return "README is currently unavailable."


def load_landing_page_html(*, user: dict | None, is_admin: bool, fcr) -> str:
	nav_config = fcr.find("navbar_landing.json")
	items = filter_nav_items(
		nav_config.get("items", []),
		user,
		is_admin,
	)
	section_cards: list[str] = []

	def _landing_nav_hero(title: str, lead: str) -> str:
		return (
			"<div class=\"landing-nav__hero\">"
			f"<h2>{html.escape(title)}</h2>"
			f"<p class=\"landing-nav__lead\">{html.escape(lead)}</p>"
			"</div>"
		)

	def _landing_nav_shell(hero_html: str, cards_html: str) -> str:
		return (
			"<section class=\"landing-nav\">"
			f"{hero_html}"
			"<div class=\"landing-nav__grid\">"
			f"{cards_html}"
			"</div>"
			"</section>"
		)

	def _card(title: str, links_html: str, *, class_name: str = "") -> str:
		card_class = "landing-nav__card"
		if class_name:
			card_class = f"{card_class} {class_name}"
		return (
			f"<div class=\"{card_class}\">"
			f"<h3>{html.escape(title)}</h3>"
			f"<div class=\"landing-nav__links\">{links_html}</div>"
			"</div>"
		)

	def _link(label: str, desc: str, href: str) -> str:
		escaped_desc = html.escape(desc)
		escaped_desc = escaped_desc.replace("&lt;br&gt;", "\n").replace("&lt;br/&gt;", "\n").replace("&lt;br /&gt;", "\n")
		return (
			f"<a class=\"landing-nav__link\" href=\"{html.escape(href)}\">"
			f"<span class=\"landing-nav__label\">{html.escape(label)}</span>"
			f"<span class=\"landing-nav__desc\">{escaped_desc}</span>"
			"</a>"
		)

	quick_links: list[str] = [
		_link("Login", "Sign in to your account.", "/login"),
		_link("Register", "Create an account to access more features.", "/register"),
	]
	for item in items:
		item_type = item.get("type")
		if item_type == "link":
			quick_links.append(_link(item.get("label", ""), item.get("desc", ""), item.get("href", "#")))
			continue
		if item_type != "mega":
			continue
		for section in item.get("sections", []):
			section_type = section.get("type")
			if section_type == "github_repos":
				username = section.get("username", "")
				limit = int(section.get("limit", 6) or 6)
				repos, total = parent_builder.fetch_github_repos(username, limit=limit) if username else ([], 0)
				links = [
					_link(
						r.get("label", ""),
						r.get("desc", ""),
						r.get("href", "#"),
					)
					for r in repos
				]
				more_count = max(total - len(repos), 0)
				if more_count:
					links.append(_link(
						f"{more_count} more repos publicly available",
						"View all repositories on GitHub.",
						f"https://github.com/{username}?tab=repositories",
					))
				section_cards.append(_card(
					section.get("label", "GitHub Repositories"),
					"".join(links),
					class_name="landing-nav__card--github",
				))
				continue
			links = [
				_link(
					entry.get("label", ""),
					entry.get("desc", ""),
					entry.get("href", "#"),
				)
				for entry in section.get("items", [])
			]
			section_cards.append(_card(section.get("label", item.get("label", "Explore")), "".join(links)))

	if quick_links:
		section_cards.insert(0, _card("Quick Links", "".join(quick_links)))

	hero_html = _landing_nav_hero(
		"This is the collection of features implemented on the website.",
		"Thank you for visiting!",
	)
	return _landing_nav_shell(hero_html, "".join(section_cards))


def _build_profile_popugame_history_html(interface, user: dict) -> str:
	try:
		user_id = str(user.get("id"))
		rating_rows, _ = interface.client.get_rows_with_filters(
			"popugame_ratings",
			equalities={"user_id": user_id},
			page_limit=1,
			page_num=0,
		)
		elo = int(rating_rows[0].get("elo") or 1200) if rating_rows else 1200
		history_rows = interface.execute_query(
			"SELECT code, player0_user_id, player1_user_id, player0_name, player1_name, winner, "
			"elo_before_p0, elo_after_p0, elo_delta_p0, "
			"elo_before_p1, elo_after_p1, elo_delta_p1, "
			"COALESCE(last_move_at, updated_at, created_at) AS ts "
			"FROM popugame_sessions "
			"WHERE status = 'finished' AND (player0_user_id = %s OR player1_user_id = %s) "
			"ORDER BY ts DESC LIMIT %s;",
			(user_id, user_id, 20),
		) or []
		wins = 0
		losses = 0
		draws = 0
		boxes: list[dict[str, str]] = []

		def _public_pname(name: object) -> str:
			n = (name or "").strip() if isinstance(name, str) else ""
			if not n:
				return "Unknown"
			return "Anonymous" if n.startswith("anon:") else n

		for row in history_rows:
			is_p0 = str(row.get("player0_user_id") or "") == user_id
			winner = row.get("winner")
			if winner is None:
				outcome = "draw"
				draws += 1
			elif (is_p0 and int(winner) == 0) or ((not is_p0) and int(winner) == 1):
				outcome = "win"
				wins += 1
			else:
				outcome = "loss"
				losses += 1
			opponent = _public_pname(row.get("player1_name") if is_p0 else row.get("player0_name"))
			delta_raw = row.get("elo_delta_p0") if is_p0 else row.get("elo_delta_p1")
			elo_after_raw = row.get("elo_after_p0") if is_p0 else row.get("elo_after_p1")
			elo_before_raw = row.get("elo_before_p0") if is_p0 else row.get("elo_before_p1")
			elo_at_match: int | None = None
			if elo_after_raw is not None:
				elo_at_match = int(elo_after_raw)
			elif elo_before_raw is not None and delta_raw is not None:
				elo_at_match = int(elo_before_raw) + int(delta_raw)

			if delta_raw is None:
				delta_txt = "Unrated game"
			else:
				delta_val = int(delta_raw)
				sign = "+" if delta_val > 0 else ""
				delta_txt = f"ELO {sign}{delta_val}"

			if elo_at_match is None:
				elo_txt = "ELO at match: unavailable"
			else:
				elo_txt = f"ELO at match: {elo_at_match}"
			boxes.append({
				"outcome": outcome,
				"tooltip": f"vs {opponent} | {outcome.upper()} | {elo_txt} | {delta_txt}",
			})

		decisive = wins + losses
		total_wr = (wins * 100.0 / decisive) if decisive > 0 else 0.0
		return html_fragments.profile_popugame_history_card(
			elo=elo,
			total_wr=total_wr,
			wins=wins,
			losses=losses,
			draws=draws,
			boxes=boxes,
		)
	except Exception:
		return html_fragments.profile_popugame_history_card(
			elo=1200,
			total_wr=0.0,
			wins=0,
			losses=0,
			draws=0,
			boxes=[],
		)


def load_profile_page_html(interface, user: dict, *, is_admin: bool) -> dict[str, str]:
	user_name = f"{user['first_name']} {user['last_name']}"
	admin_since = None
	if is_admin:
		try:
			rows, _ = interface.client.get_rows_with_filters(
				"admins",
				equalities={"user_id": user.get("id")},
				page_limit=1,
				page_num=0,
			)
			if rows:
				admin_since = rows[0].get("created_at")
		except Exception:
			admin_since = None
	admin_line = ""
	if admin_since:
		admin_line = html_fragments.profile_admin_line(admin_since.strftime("%d %B %Y"))

	integration_cards: list[tuple[int, str]] = []
	try:
		webhooks, _ = interface.client.get_rows_with_filters(
			"discord_webhooks",
			equalities={"user_id": user.get("id")},
			page_limit=50,
			page_num=0,
		)
		for webhook in webhooks or []:
			subscriptions_html = ""
			try:
				sub_rows = interface.get_discord_webhook_subscriptions(webhook.get("id"))
				if sub_rows:
					sub_cards = []
					for sub in sub_rows:
						perm = (sub.get("permission") or "unknown").upper()
						desc = sub.get("description") or ""
						created = sub.get("created_at")
						date_str = ""
						if created:
							try:
								date_str = created.strftime("%d %B %Y")
							except Exception:
								date_str = str(created)
						is_active = bool(sub.get("is_active", True))
						status_label = "Active" if is_active else "Inactive"
						unsubscribe_html = ""
						if is_active:
							unsubscribe_html = html_fragments.subscription_action(
								"unsubscribe",
								str(sub.get("id")),
								"/api/profile/discord-webhook/unsubscribe",
								"Unsubscribe",
							)
						resubscribe_html = ""
						if not is_active:
							resubscribe_html = html_fragments.subscription_action(
								"resubscribe",
								str(sub.get("id")),
								"/api/profile/discord-webhook/resubscribe",
								"Resubscribe",
							)
						sub_cards.append(
							html_fragments.subscription_card(
								sub.get("event_key") or "",
								perm,
								desc,
								date_str,
								status_label,
								is_active,
								unsubscribe_html,
								resubscribe_html,
							)
						)
					subscriptions_html = html_fragments.integration_subscriptions(
						"Subscriptions",
						"".join(sub_cards),
					)
				else:
					subscriptions_html = html_fragments.integration_subscriptions_empty("Subscriptions")
			except Exception:
				subscriptions_html = ""

			status = "Active" if webhook.get("is_active", True) else "Suspended"
			delete_button = ""
			badge_html = html_fragments.integration_badge(status)
			if status == "Active":
				delete_button = html_fragments.integration_delete_action(
					"discord_webhook",
					str(webhook.get("id")),
					"Discord Webhook",
					True,
				)
				integration_cards.append((
					0 if status == "Active" else 1,
					html_fragments.integration_card(
						"discord_webhook",
						str(webhook.get("id")),
						"Discord Webhook",
						html.escape(webhook.get("name") or "Webhook"),
						html_fragments.secret_field(webhook.get("webhook_url") or "", label="Webhook URL"),
						badge_html + delete_button,
						subscriptions_html,
						status=status,
					)
				))
	except Exception:
		pass

	try:
		whitelist_rows, _ = interface.client.get_rows_with_filters(
			"minecraft_whitelist",
			equalities={"user_id": user.get("id")},
			page_limit=5,
			page_num=0,
		)
		for row in whitelist_rows or []:
			joined = ""
			if row.get("whitelisted_at"):
				try:
					joined = row["whitelisted_at"].strftime("%d %B %Y")
				except Exception:
					joined = str(row["whitelisted_at"])
			status = "Whitelisted" if row.get("is_active", True) else "Suspended"
			delete_button = ""
			badge_html = html_fragments.integration_badge(status)
			if status == "Whitelisted":
				delete_button = html_fragments.integration_delete_action(
					"minecraft",
					str(row.get("id")),
					"Minecraft",
					True,
				)
				integration_cards.append((
					0 if status == "Whitelisted" else 1,
					html_fragments.integration_card(
						"minecraft",
						str(row.get("id")),
						"Minecraft",
						f"Username: {html.escape(row.get('mc_username') or '')}",
						f"Whitelisted {html.escape(joined) if joined else ''}",
						badge_html + delete_button,
						status=status,
					)
				))
	except Exception:
		pass

	try:
		abs_rows, _ = interface.client.get_rows_with_filters(
			"audiobookshelf_registrations",
			equalities={"user_id": user.get("id"), "status": "approved"},
			page_limit=1,
			page_num=0,
		)
		if abs_rows:
			row = abs_rows[0]
			approved_at = ""
			if row.get("reviewed_at"):
				try:
					approved_at = row["reviewed_at"].strftime("%d %B %Y")
				except Exception:
					approved_at = str(row["reviewed_at"])
			status = "Approved" if row.get("is_active", True) else "Suspended"
			delete_button = ""
			badge_html = html_fragments.integration_badge(status)
			if status == "Approved":
				delete_button = html_fragments.integration_delete_action(
					"audiobookshelf",
					str(row.get("id")),
					"Audiobookshelf",
					True,
				)
				integration_cards.append((
					0 if status == "Approved" else 1,
					html_fragments.integration_card(
						"audiobookshelf",
						str(row.get("id")),
						"Audiobookshelf",
						html.escape(row.get("email") or user.get("email") or ""),
						f"Approved {html.escape(approved_at) if approved_at else ''}",
						badge_html + delete_button,
						status=status,
					)
				))
	except Exception:
		pass

	if not integration_cards:
		integration_cards.append((0, html_fragments.integration_card_empty()))
	integration_cards.sort(key=lambda item: item[0])
	integration_cards_html = "".join(card for _, card in integration_cards)
	popugame_history_html = _build_profile_popugame_history_html(interface, user)
	profile_panels = html_fragments.profile_password_panel() + html_fragments.profile_delete_panel()
	profile_card_html = html_fragments.profile_card(
		initials=user["first_name"][:1] + user["last_name"][:1],
		user_name=user_name,
		created_at=user["created_at"].strftime("%d %B %Y"),
		email=user["email"],
		badge_label="ADMIN" if is_admin else "MEMBER",
		admin_line=admin_line,
		panels_html=profile_panels,
	)
	integrations_html = html_fragments.profile_integrations_header(
		"Linked integrations",
		"Services connected to your account.",
		integration_cards_html,
	)
	modal_html = html_fragments.integration_delete_modal(
		html_fragments.integration_delete_reason_select(
			[
				("", "Select a reason"),
				("no-longer-needed", "No longer needed"),
				("privacy", "Privacy/security concerns"),
				("incorrect", "Incorrect setup"),
				("switching", "Switching accounts"),
				("other", "Other"),
			]
		)
	)
	return {
		"title": user_name + "'s Profile",
		"body_html": html_fragments.profile_page_shell(
			profile_card_html + popugame_history_html + integrations_html + modal_html
		),
	}


def load_discord_webhook_registration_model(interface, user: dict | None, *, is_admin: bool) -> dict[str, Any]:
	allowed_permissions = ["all"]
	if user:
		allowed_permissions.append("users")
	if is_admin:
		allowed_permissions.append("admins")

	event_options: list[tuple[str, str]] = []
	try:
		rows, _ = interface.client.get_rows_with_filters(
			"discord_event_keys",
			raw_conditions=["permission = ANY(%s)"],
			raw_params=[allowed_permissions],
			page_limit=1000,
			page_num=0,
			order_by="event_key",
			order_dir="ASC",
		)
		for row in rows:
			label = row["event_key"]
			desc = row.get("description")
			if desc:
				label = f"{label} — {desc}"
			event_options.append((row["event_key"], label))
	except Exception:
		event_options = []

	webhook_pairs: list[dict[str, str]] = []
	if user:
		try:
			webhook_rows, _ = interface.client.get_rows_with_filters(
				"discord_webhooks",
				equalities={"user_id": user.get("id")},
				page_limit=50,
				page_num=0,
			)
			for row in webhook_rows or []:
				name = (row.get("name") or "").strip()
				url = (row.get("webhook_url") or "").strip()
				if not name or not url:
					continue
				webhook_pairs.append({"name": name, "url": url})
		except Exception:
			webhook_pairs = []

	return {
		"event_options": event_options,
		"webhook_pairs": webhook_pairs,
		"contact_required": user is None,
	}


def load_verify_email_token_model(interface, token: str) -> dict[str, str]:
	pending_user = None
	try:
		token_hash = interface._hash_verification_token(token)
		pending_rows = interface.get_pending_user({"verification_token_hash": token_hash})
		if pending_rows:
			pending_user = pending_rows[0]
	except Exception:
		pending_user = None

	validation, validation_message = interface.validate_verification_token(token)
	if validation and pending_user:
		try:
			emitter = DiscordWebhookEmitter(interface)
			emitter.emit_event(
				"moderator.notifications",
				payload={
					"embeds": [
						{
							"title": "New account created",
							"fields": [
								{"name": "Action", "value": "account_created", "inline": True},
								{"name": "Subject", "value": pending_user.get("email", ""), "inline": True},
								{
									"name": "Details",
									"value": (
										f"- Name: {pending_user.get('first_name', '')} {pending_user.get('last_name', '')}\n"
										f"- Email: {pending_user.get('email', '')}\n"
										f"- User ID: {pending_user.get('id', '')}"
									),
									"inline": False,
								},
							],
						}
					]
				},
				context={
					"action": "account_created",
					"user_id": pending_user.get("id"),
					"email": pending_user.get("email"),
				},
			)
		except Exception:
			pass

	if validation:
		return {
			"page_title": "Email Verified",
			"message_html": html_fragments.paragraph(
				"Your email has been successfully verified! You can now log in to your account."
			),
		}

	return {
		"page_title": "Verification Failed",
		"message_html": (
			html_fragments.paragraph("The verification link could not be processed.")
			+ html_fragments.paragraph_with_strong("Reason:", html.escape(validation_message))
		),
	}


def load_minecraft_page_model(interface, fcr, user: dict | None) -> dict[str, Any]:
	def minecraft_target_host() -> str:
		default_host = "mc.zubekanov.com"
		try:
			conf = fcr.find("minecraft_status.conf")
			if not isinstance(conf, dict):
				return default_host
			return (conf.get("MINECRAFT_SERVER_HOST") or "").strip() or default_host
		except Exception:
			return default_host

	is_whitelisted = False
	whitelist_username = ""
	if user:
		try:
			rows = interface.get_active_minecraft_whitelist_usernames(user.get("id"))
			if rows:
				is_whitelisted = True
				whitelist_username = rows[0].get("mc_username") or ""
		except Exception:
			is_whitelisted = False

	return {
		"host": minecraft_target_host(),
		"is_whitelisted": is_whitelisted,
		"whitelist_username": whitelist_username,
	}


def load_admin_dashboard_page_html(interface) -> str:
	count_audiobookshelf = interface.count_pending_audiobookshelf_registrations()
	count_webhook = interface.count_pending_discord_webhook_registrations()
	count_minecraft = interface.count_pending_minecraft_registrations()
	count_api_access = interface.count_pending_api_access_registrations()

	approvals_cards_html = (
		html_fragments.admin_card(
			"/admin/minecraft-approvals",
			html_fragments.admin_card_meta(
				"Approvals",
				html_fragments.admin_badge_count(count_minecraft),
			),
			"Minecraft Requests",
			"Review Minecraft whitelist requests.",
		)
		+ html_fragments.admin_card(
			"/admin/discord-webhook-approvals",
			html_fragments.admin_card_meta(
				"Approvals",
				html_fragments.admin_badge_count(count_webhook),
			),
			"Discord Webhook Requests",
			"Review webhook registration requests.",
		)
		+ html_fragments.admin_card(
			"/admin/api-access-approvals",
			html_fragments.admin_card_meta(
				"Approvals",
				html_fragments.admin_badge_count(count_api_access),
			),
			"API Access Requests",
			"Review API key access applications.",
		)
		+ html_fragments.admin_card(
			"/admin/audiobookshelf-approvals",
			html_fragments.admin_card_meta(
				"Approvals",
				html_fragments.admin_badge_count(count_audiobookshelf),
			),
			"Audiobookshelf Requests",
			"Review account registrations.",
		)
	)

	operations_cards_html = (
		html_fragments.admin_card(
			"/psql-interface",
			html_fragments.admin_card_meta("Database"),
			"Database Interface",
			"View and edit database tables.",
			class_name="admin-card--wide",
		)
		+ html_fragments.admin_card(
			"/admin/users",
			html_fragments.admin_card_meta("Accounts"),
			"User Management",
			"View users, roles, and integrations.",
		)
	)

	diagnostics_cards_html = (
		html_fragments.admin_card(
			"/admin/email-debug",
			html_fragments.admin_card_meta("Tools"),
			"Debug Email",
			"Send a test email from the system.",
		)
		+ html_fragments.admin_card(
			"/admin/frontend-test",
			html_fragments.admin_card_meta("Tools"),
			"Frontend Test Page",
			"Preview labeled UI elements and styles.",
		)
	)
	cards_html = (
		html_fragments.admin_dashboard_section("Approvals", approvals_cards_html)
		+ html_fragments.admin_dashboard_section("Operations", operations_cards_html)
		+ html_fragments.admin_dashboard_section("Diagnostics", diagnostics_cards_html, compact=True)
	)
	return html_fragments.admin_dashboard(cards_html)


def _get_user_status_label(interface, user_id: str | None, user_cache: dict[str, dict]) -> tuple[str, dict]:
	if not user_id:
		return "Anonymous", {}
	uid = str(user_id)
	if uid not in user_cache:
		user_rows, _ = interface.client.get_rows_with_filters(
			"users",
			equalities={"id": uid},
			page_limit=1,
			page_num=0,
		)
		user_cache[uid] = user_rows[0] if user_rows else {}
	user_row = user_cache.get(uid, {})
	if user_row.get("is_anonymous"):
		return "Anonymous", user_row
	if interface.is_admin(uid):
		return "Admin", user_row
	return "Member", user_row


def load_admin_users_page_html(interface) -> str:
	user_rows = interface.get_admin_user_management_rows()
	cards_html: list[str] = []

	for row in user_rows:
		user_id = str(row.get("id") or "")
		first = row.get("first_name") or ""
		last = row.get("last_name") or ""
		email = row.get("email") or ""
		created = row.get("created_at")
		created_str = ""
		if created:
			try:
				created_str = created.strftime("%d %B %Y")
			except Exception:
				created_str = str(created)
		is_anonymous = bool(row.get("is_anonymous"))
		is_active = bool(row.get("is_active", True))
		role_label = "ANONYMOUS" if is_anonymous else ("ADMIN" if interface.is_admin(user_id) else "MEMBER")
		status_label = "Active" if is_active else "Inactive"
		meta_html = (
			html_fragments.admin_user_meta_row("Joined", created_str or "Unknown")
			+ html_fragments.admin_user_meta_row("Status", status_label)
			+ html_fragments.admin_user_meta_row("User ID", user_id)
		)
		badge_html = html_fragments.admin_user_badge(role_label)
		actions = []
		if not is_anonymous:
			if role_label == "ADMIN":
				actions.append(html_fragments.admin_user_action_button("demote", user_id, "Demote to member", True))
			else:
				actions.append(html_fragments.admin_user_action_button("promote", user_id, "Promote to admin"))
			actions.append(html_fragments.admin_user_action_button("delete", user_id, "Delete user", True))
		actions_html = html_fragments.admin_user_actions("".join(actions))

		integration_cards: list[tuple[int, str]] = []
		try:
			webhooks, _ = interface.client.get_rows_with_filters(
				"discord_webhooks",
				equalities={"user_id": user_id},
				page_limit=50,
				page_num=0,
				)
			for webhook in webhooks or []:
				subscriptions_html = ""
				try:
					sub_rows = interface.get_discord_webhook_subscriptions(webhook.get("id"))
					if sub_rows:
						sub_cards = []
						for sub in sub_rows:
							perm = (sub.get("permission") or "unknown").upper()
							desc = sub.get("description") or ""
							created_at = sub.get("created_at")
							date_str = ""
							if created_at:
								try:
									date_str = created_at.strftime("%d %B %Y")
								except Exception:
									date_str = str(created_at)
							is_sub_active = bool(sub.get("is_active", True))
							status = "Active" if is_sub_active else "Inactive"
							sub_cards.append(
								html_fragments.subscription_card(
									sub.get("event_key") or "",
									perm,
									desc,
									date_str,
									status,
									is_sub_active,
									"",
									"",
								)
							)
						subscriptions_html = html_fragments.integration_subscriptions(
							"Subscriptions",
							"".join(sub_cards),
						)
					else:
						subscriptions_html = html_fragments.integration_subscriptions_empty("Subscriptions")
				except Exception:
					subscriptions_html = ""

				status = "Active" if webhook.get("is_active", True) else "Suspended"
				badge = html_fragments.integration_badge(status)
				if status == "Active":
					delete_button = html_fragments.integration_delete_action(
						"discord_webhook",
						str(webhook.get("id")),
						"Discord Webhook",
						True,
						user_id=user_id,
						submit_route="/api/admin/users/integration/disable",
						active_label="Active",
					)
				else:
					delete_button = (
						html_fragments.integration_enable_action(
							"discord_webhook",
							str(webhook.get("id")),
							"Discord Webhook",
							"Active",
							user_id=user_id,
							submit_route="/api/admin/users/integration/enable",
						)
						+ html_fragments.integration_delete_action(
							"discord_webhook",
							str(webhook.get("id")),
							"Discord Webhook",
							False,
							user_id=user_id,
							submit_route="/api/admin/users/integration/disable",
							hidden=True,
						)
					)
				integration_cards.append((
					0 if status == "Active" else 1,
					html_fragments.integration_card(
						"discord_webhook",
						str(webhook.get("id")),
						"Discord Webhook",
						html.escape(webhook.get("name") or "Webhook"),
						html_fragments.secret_field(webhook.get("webhook_url") or "", label="Webhook URL"),
						badge + delete_button,
						subscriptions_html,
						status=status,
					)
				))
		except Exception:
			pass

		try:
			whitelist_rows, _ = interface.client.get_rows_with_filters(
				"minecraft_whitelist",
				equalities={"user_id": user_id},
				page_limit=5,
				page_num=0,
			)
			for whitelist in whitelist_rows or []:
				joined = ""
				if whitelist.get("whitelisted_at"):
					try:
						joined = whitelist["whitelisted_at"].strftime("%d %B %Y")
					except Exception:
						joined = str(whitelist["whitelisted_at"])
					status = "Whitelisted" if whitelist.get("is_active", True) else "Suspended"
					badge = html_fragments.integration_badge(status)
					if status == "Whitelisted":
						delete_button = html_fragments.integration_delete_action(
							"minecraft",
							str(whitelist.get("id")),
							"Minecraft",
							True,
							user_id=user_id,
							submit_route="/api/admin/users/integration/disable",
							active_label="Whitelisted",
						)
					else:
						delete_button = (
							html_fragments.integration_enable_action(
								"minecraft",
								str(whitelist.get("id")),
								"Minecraft",
								"Whitelisted",
								user_id=user_id,
								submit_route="/api/admin/users/integration/enable",
							)
							+ html_fragments.integration_delete_action(
								"minecraft",
								str(whitelist.get("id")),
								"Minecraft",
								False,
								user_id=user_id,
								submit_route="/api/admin/users/integration/disable",
								hidden=True,
							)
						)
					integration_cards.append((
						0 if status == "Whitelisted" else 1,
						html_fragments.integration_card(
							"minecraft",
							str(whitelist.get("id")),
							"Minecraft",
							f"Username: {html.escape(whitelist.get('mc_username') or '')}",
							f"Whitelisted {html.escape(joined) if joined else ''}",
							badge + delete_button,
							status=status,
						)
					))
		except Exception:
			pass

		try:
			abs_rows, _ = interface.client.get_rows_with_filters(
				"audiobookshelf_registrations",
				equalities={"user_id": user_id, "status": "approved"},
				page_limit=1,
				page_num=0,
			)
			if abs_rows:
				abs_row = abs_rows[0]
				approved_at = ""
				if abs_row.get("reviewed_at"):
					try:
						approved_at = abs_row["reviewed_at"].strftime("%d %B %Y")
					except Exception:
						approved_at = str(abs_row["reviewed_at"])
					status = "Approved" if abs_row.get("is_active", True) else "Suspended"
					badge = html_fragments.integration_badge(status)
					if status == "Approved":
						delete_button = html_fragments.integration_delete_action(
							"audiobookshelf",
							str(abs_row.get("id")),
							"Audiobookshelf",
							True,
							user_id=user_id,
							submit_route="/api/admin/users/integration/disable",
							active_label="Approved",
						)
					else:
						delete_button = (
							html_fragments.integration_enable_action(
								"audiobookshelf",
								str(abs_row.get("id")),
								"Audiobookshelf",
								"Approved",
								user_id=user_id,
								submit_route="/api/admin/users/integration/enable",
							)
							+ html_fragments.integration_delete_action(
								"audiobookshelf",
								str(abs_row.get("id")),
								"Audiobookshelf",
								False,
								user_id=user_id,
								submit_route="/api/admin/users/integration/disable",
								hidden=True,
							)
						)
					integration_cards.append((
						0 if status == "Approved" else 1,
						html_fragments.integration_card(
							"audiobookshelf",
							str(abs_row.get("id")),
							"Audiobookshelf",
							html.escape(abs_row.get("email") or email),
							f"Approved {html.escape(approved_at) if approved_at else ''}",
							badge + delete_button,
							status=status,
						)
					))
		except Exception:
			pass

		if not integration_cards:
			display_name = (first + " " + last).strip() or "This user"
			integration_cards.append((
				0,
				html_fragments.integration_card_empty(
					f"{display_name} has not connected any services yet."
				),
			))
		integration_cards.sort(key=lambda item: item[0])
		integrations_html = html_fragments.admin_user_integrations(
			"".join(card for _, card in integration_cards)
		)

		cards_html.append(
			html_fragments.admin_user_card(
				user_id=user_id,
				name=(first + " " + last).strip() or "Unknown",
				email=email or "Unknown",
				meta_html=meta_html,
				badge_html=badge_html,
				actions_html=actions_html,
				integrations_html=integrations_html,
				role_label=role_label,
			)
		)

	return (
		html_fragments.admin_users_shell("".join(cards_html))
		+ html_fragments.integration_delete_modal(
			html_fragments.integration_delete_reason_select(
				[
					("", "Select a reason"),
					("admin", "Admin action"),
					("policy", "Policy violation"),
					("security", "Security concern"),
					("other", "Other"),
				]
			)
		)
		+ html_fragments.admin_user_delete_modal(
			html_fragments.admin_user_delete_reason_select(
				[
					("", "Select a reason"),
					("requested", "User requested removal"),
					("policy", "Policy violation"),
					("security", "Security concern"),
					("duplicate", "Duplicate account"),
					("other", "Other"),
				]
			)
		)
	)


def load_admin_approvals_page_html(interface, kind: str) -> dict[str, str]:
	user_cache: dict[str, dict] = {}
	if kind == "audiobookshelf":
		title = "Audiobookshelf Approvals"
		rows, _ = interface.client.get_rows_with_filters(
			"audiobookshelf_registrations",
			equalities={"status": "pending"},
			page_limit=200,
			page_num=0,
			order_by="created_at",
			order_dir="DESC",
		)
		cards_html: list[str] = []
		for row in rows:
			status_label, _ = _get_user_status_label(interface, row.get("user_id"), user_cache)
			name = f"{row['first_name']} {row['last_name']}".strip()
			email = row["email"]
			additional = row.get("additional_info") or ""
			rows_html = (
				html_fragments.approval_row("Email", html.escape(email))
				+ html_fragments.approval_row("Additional info", html.escape(additional) if additional else "—", full=True)
			)
			actions_html = html_fragments.approval_actions(
				"/api/admin/audiobookshelf/approve",
				"/api/admin/audiobookshelf/deny",
				str(row["id"]),
			)
			cards_html.append(
				html_fragments.approval_card(
					html.escape(name),
					"Audiobookshelf Request",
					html.escape(status_label),
					rows_html,
					actions_html,
				)
			)
		return {"title": title, "cards_html": "".join(cards_html)}

	if kind == "discord-webhook":
		title = "Discord Webhook Approvals"
		rows, _ = interface.client.get_rows_with_filters(
			"discord_webhook_registrations",
			equalities={"status": "pending"},
			page_limit=200,
			page_num=0,
			order_by="created_at",
			order_dir="DESC",
		)
		cards_html = []
		for row in rows:
			submitted = ""
			submitted_at = ""
			uid = ""
			if row.get("created_at"):
				try:
					submitted_at = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
				except Exception:
					submitted_at = str(row["created_at"])
			if submitted_at:
				submitted_at = f"{html.escape(submitted_at)}"
			if row.get("submitted_by_name") or row.get("submitted_by_email"):
				name = row.get("submitted_by_name") or "Unknown"
				email = row.get("submitted_by_email") or "Unknown"
				submitted = f"{html.escape(name)} — {html.escape(email)}"
				status_label = "Anonymous"
			elif row.get("submitted_by_user_id"):
				uid = str(row["submitted_by_user_id"])
				status_label, cached_user = _get_user_status_label(interface, uid, user_cache)
				name = f"{cached_user.get('first_name','')} {cached_user.get('last_name','')}".strip() or "Unknown"
				email = cached_user.get("email") or "Unknown"
				submitted = f"{html.escape(name)} — {html.escape(email)}"
			else:
				status_label = "Anonymous"
			rows_html = (
				html_fragments.approval_row("Webhook URL", html.escape(row["webhook_url"]))
				+ html_fragments.approval_row("Submitted by", submitted or "Anonymous")
				+ html_fragments.approval_row("Submitted at", submitted_at or "—")
				+ html_fragments.approval_row("User ID", html.escape(uid) if row.get("submitted_by_user_id") else "—")
			)
			actions_html = html_fragments.approval_actions(
				"/api/admin/discord-webhook/approve",
				"/api/admin/discord-webhook/deny",
				str(row["id"]),
			)
			cards_html.append(
				html_fragments.approval_card(
					html.escape(row["name"]),
					f"Event key: {html.escape(row['event_key'])}",
					html.escape(status_label),
					rows_html,
					actions_html,
				)
			)
		return {"title": title, "cards_html": "".join(cards_html)}

	if kind == "minecraft":
		title = "Minecraft Approvals"
		rows, _ = interface.client.get_rows_with_filters(
			"minecraft_registrations",
			equalities={"status": "pending"},
			page_limit=200,
			page_num=0,
			order_by="created_at",
			order_dir="DESC",
		)
		cards_html = []
		for row in rows:
			submitted_at = ""
			if row.get("created_at"):
				try:
					submitted_at = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
				except Exception:
					submitted_at = str(row["created_at"])
			submitted_at = html.escape(submitted_at) if submitted_at else ""
			status_label, _ = _get_user_status_label(interface, row.get("user_id"), user_cache)
			rows_html = (
				html_fragments.approval_row("Name", f"{html.escape(row['first_name'])} {html.escape(row['last_name'])}")
				+ html_fragments.approval_row("Email", html.escape(row["email"]))
				+ html_fragments.approval_row("Submitted at", submitted_at or "—")
				+ html_fragments.approval_row("Additional info", html.escape(row.get("additional_info") or "") or "—", full=True)
			)
			actions_html = html_fragments.approval_actions(
				"/api/admin/minecraft/approve",
				"/api/admin/minecraft/deny",
				str(row["id"]),
			)
			cards_html.append(
				html_fragments.approval_card(
					html.escape(row["mc_username"]),
					html.escape(row["who_are_you"]),
					html.escape(status_label),
					rows_html,
					actions_html,
				)
			)
		return {"title": title, "cards_html": "".join(cards_html)}

	title = "API Access Approvals"
	rows, _ = interface.client.get_rows_with_filters(
		"api_access_registrations",
		equalities={"status": "pending"},
		page_limit=200,
		page_num=0,
		order_by="created_at",
		order_dir="DESC",
	)
	cards_html = []
	for row in rows:
		status_label, _ = _get_user_status_label(interface, row.get("user_id"), user_cache)
		submitted_at = ""
		if row.get("created_at"):
			try:
				submitted_at = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
			except Exception:
				submitted_at = str(row["created_at"])
		submitted_at = html.escape(submitted_at) if submitted_at else ""
		name = f"{row.get('first_name', '')} {row.get('last_name', '')}".strip() or row.get("email", "Unknown")
		requested_scopes = row.get("requested_scopes") or []
		if isinstance(requested_scopes, list):
			scopes_text = ", ".join(str(scope) for scope in requested_scopes if scope)
		else:
			scopes_text = str(requested_scopes)
		rows_html = (
			html_fragments.approval_row("Email", html.escape(row.get("email") or "—"))
			+ html_fragments.approval_row("Principal", html.escape(row.get("principal_type") or "—"))
			+ html_fragments.approval_row("Service", html.escape(row.get("service_name") or "—"))
			+ html_fragments.approval_row("Submitted at", submitted_at or "—")
			+ html_fragments.approval_row("Scopes", html.escape(scopes_text) if scopes_text else "—", full=True)
			+ html_fragments.approval_row("Use case", html.escape(row.get("use_case") or "—"), full=True)
		)
		actions_html = html_fragments.approval_actions(
			"/api/admin/api-access/approve",
			"/api/admin/api-access/deny",
			str(row["id"]),
		)
		cards_html.append(
			html_fragments.approval_card(
				html.escape(name),
				"API Access Request",
				html.escape(status_label),
				rows_html,
				actions_html,
			)
		)
	return {"title": title, "cards_html": "".join(cards_html)}


def load_psql_interface_page_html(interface) -> str:
	schema = "public"
	user_lookup = {}
	user_options = []
	try:
		user_rows, _ = interface.client.get_rows_with_filters("users", page_limit=1000, page_num=0)
		for user in user_rows:
			uid = user.get("id")
			if uid:
				user_lookup[str(uid)] = user
				label_bits = [
					f"{user.get('first_name','')} {user.get('last_name','')}".strip(),
					user.get("email", ""),
				]
				label = " — ".join([bit for bit in label_bits if bit])
				user_options.append({
					"id": str(uid),
					"label": label,
				})
	except Exception:
		user_lookup = {}
		user_options = []

	if user_options:
		options_json = json.dumps(user_options)
		options_b64 = base64.b64encode(options_json.encode("utf-8")).decode("ascii")
		user_options_script = html_fragments.db_user_id_options_script(options_b64)
	else:
		user_options_script = ""

	html_parts = [
		html_fragments.db_admin_open(),
		html_fragments.heading("Database Admin", 1),
		html_fragments.db_admin_message(),
	]
	if user_options_script:
		html_parts.append(user_options_script)

	tables = interface.client.list_tables(schema)
	enum_map: dict[str, dict[str, list[str]]] = {}
	tables_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sql", "tables"))
	try:
		for filename in os.listdir(tables_dir):
			if not filename.endswith(".json"):
				continue
			with open(os.path.join(tables_dir, filename), "r", encoding="utf-8") as handle:
				cfg = json.load(handle)
			tables_cfg = []
			if isinstance(cfg, list):
				tables_cfg = cfg
			elif isinstance(cfg, dict) and "tables" in cfg and isinstance(cfg["tables"], list):
				tables_cfg = cfg["tables"]
			elif isinstance(cfg, dict) and "table_name" in cfg:
				tables_cfg = [cfg]
			for table_cfg in tables_cfg:
				table_name = table_cfg.get("table_name")
				if not table_name:
					continue
				for col in table_cfg.get("columns", []):
					enum_vals = col.get("enum")
					if not enum_vals:
						continue
					enum_map.setdefault(table_name, {})[col["name"]] = [str(value) for value in enum_vals]
	except Exception:
		enum_map = {}

	for table in tables:
		columns = interface.client.get_table_columns(schema, table)
		col_info = interface.client.get_column_info(schema, table)
		pk_cols = interface.client.get_primary_key_columns(schema, table)
		table_enums = enum_map.get(table, {})
		rows, _ = interface.client.get_rows_with_filters(
			f"{schema}.{table}",
			page_limit=200,
			page_num=0,
		)

		html_parts.append(html_fragments.db_section_open(table))

		if not pk_cols:
			html_parts.append(html_fragments.db_section_no_pk())
			continue

		grid_cols = " ".join(["minmax(0, 1fr)"] * len(columns) + ["160px"])
		col_types = ",".join([(col_info.get(col, {}).get("data_type") or "") for col in columns])
		pk_cols_attr = ",".join(pk_cols)
		html_parts.append(html_fragments.db_grid_open(
			grid_cols=grid_cols,
			col_count=len(columns),
			columns=columns,
			col_types=col_types,
			pk_cols_attr=pk_cols_attr,
		))
		html_parts.append(html_fragments.db_grid_head_row(columns))

		if not rows:
			html_parts.append(html_fragments.db_grid_empty_row())
		else:
			for row in rows:
				field_names = ["table", "schema"]
				field_names.extend([f"pk__{pk}" for pk in pk_cols])
				field_names.extend([f"col__{col}" for col in columns])
				fields_attr = html.escape(", ".join(field_names))

				html_parts.append(html_fragments.db_row_form_open())
				html_parts.append(_hidden_input("table", table))
				html_parts.append(_hidden_input("schema", schema))

				for pk in pk_cols:
					pk_val = row.get(pk)
					html_parts.append(_hidden_input(
						f"pk__{pk}",
						str(pk_val) if pk_val is not None else "",
					))

				for index, col in enumerate(columns):
					val = row.get(col)
					val_str = "" if val is None else str(val)
					max_len = col_info.get(col, {}).get("character_maximum_length")
					col_type = (col_info.get(col, {}).get("data_type") or "").lower()
					enum_vals = table_enums.get(col)
					if enum_vals:
						options_html = html_fragments.db_enum_options(enum_vals, selected=val_str, include_blank=True)
						html_parts.append(html_fragments.db_cell_enum(index, col, options_html))
					elif col_type == "boolean":
						html_parts.append(html_fragments.db_cell_checkbox(index, col, bool(val)))
					else:
						tooltip_attr = ""
						tooltip_class = ""
						if col == "user_id" and val is not None:
							u = user_lookup.get(str(val))
							if u:
								title_bits = [
									f"{u.get('first_name', '')} {u.get('last_name', '')}".strip(),
									u.get("email", ""),
								]
								tooltip_attr = f' data-tooltip="{html.escape(" | ".join([bit for bit in title_bits if bit]))}"'
								tooltip_class = " db-cell--tooltip"
						html_parts.append(html_fragments.db_cell_text(
							i=index,
							col=col,
							val_str=val_str,
							max_len=int(max_len) if max_len else None,
							user_id_input=col.endswith("user_id") and bool(user_options),
							tooltip_attr=tooltip_attr,
							tooltip_class=tooltip_class,
						))

				html_parts.append(html_fragments.db_actions_cell(fields_attr))
				html_parts.append(html_fragments.db_row_form_close())

		html_parts.append(html_fragments.db_add_row_head())
		insert_fields = ["table", "schema"]
		insert_fields.extend([f"col__{col}" for col in columns])
		insert_fields_attr = html.escape(", ".join(insert_fields))
		html_parts.append(html_fragments.db_row_add_open())
		html_parts.append(_hidden_input("table", table))
		html_parts.append(_hidden_input("schema", schema))

		for index, col in enumerate(columns):
			max_len = col_info.get(col, {}).get("character_maximum_length")
			col_type = (col_info.get(col, {}).get("data_type") or "").lower()
			enum_vals = table_enums.get(col)
			if enum_vals:
				options_html = html_fragments.db_enum_options(enum_vals, include_blank=True)
				html_parts.append(html_fragments.db_cell_enum(index, col, options_html))
			elif col_type == "boolean":
				html_parts.append(html_fragments.db_cell_checkbox(index, col, False))
			else:
				html_parts.append(html_fragments.db_cell_text(
					i=index,
					col=col,
					val_str="",
					max_len=int(max_len) if max_len else None,
					user_id_input=col.endswith("user_id") and bool(user_options),
					tooltip_attr="",
					tooltip_class="",
				))

		html_parts.append(html_fragments.db_add_actions_cell(insert_fields_attr))
		html_parts.append(html_fragments.db_row_add_close())

		html_parts.append(html_fragments.db_grid_close())
		html_parts.append(html_fragments.db_section_close())

	html_parts.append(html_fragments.db_admin_close())
	return "".join(html_parts)
