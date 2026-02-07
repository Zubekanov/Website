from abc import ABC
import re

from util.fcr.file_config_reader import FileConfigReader
from flask import has_request_context, render_template_string, request
import pandas as pd

from util.webpage_builder.metrics_builder import METRICS_NAMES
from util.navbars.visibility import filter_nav_items, nav_entry_visible
import html
import json
import time
import urllib.request

fcr = FileConfigReader()
try:
	user_navbar_config = fcr.find("user_account.json")
except FileNotFoundError:
	user_navbar_config = {}
_GITHUB_REPO_CACHE: dict[str, dict[str, object]] = {}

def _fetch_github_repos(username: str, limit: int = 6) -> tuple[list[dict], int]:
	cache = _GITHUB_REPO_CACHE.get(username)
	now = time.time()
	if cache and now - cache.get("fetched_at", 0) < 1800:
		repos = cache.get("repos", [])
		total = int(cache.get("total", len(repos)))
		return repos[:limit], total

	repos: list[dict] = []
	total = 0
	try:
		url = f"https://api.github.com/users/{username}/repos?per_page=100&sort=updated"
		req = urllib.request.Request(url, headers={"User-Agent": "Website_Dev"})
		with urllib.request.urlopen(req, timeout=5) as resp:
			data = json.load(resp)
		if isinstance(data, list):
			for repo in data:
				if repo.get("fork"):
					continue
				repos.append({
					"label": repo.get("name") or "",
					"desc": repo.get("description") or "GitHub repository",
					"href": repo.get("html_url") or "#",
					"updated_at": repo.get("updated_at") or "",
				})
			total = len(repos)
		if total == 0:
			user_url = f"https://api.github.com/users/{username}"
			user_req = urllib.request.Request(user_url, headers={"User-Agent": "Website_Dev"})
			with urllib.request.urlopen(user_req, timeout=5) as resp:
				user_data = json.load(resp)
			total = int(user_data.get("public_repos", 0) or 0)
		repos.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
	except Exception:
		if cache:
			repos = cache.get("repos", [])
			total = int(cache.get("total", len(repos)))
		else:
			repos = []
			total = 0

	_GITHUB_REPO_CACHE[username] = {"fetched_at": now, "repos": repos, "total": total}
	return repos[:limit], total

def fetch_github_repos(username: str, limit: int = 6) -> tuple[list[dict], int]:
	return _fetch_github_repos(username, limit=limit)

BUILD_MS = "__BUILD_MS__"

_default_footer_html = fcr.find("default_footer.html")
DEFAULT_SITE_TITLE = "Joseph Wong"
DEFAULT_SITE_DESCRIPTION = "Personal website hosting various projects and information."
DEFAULT_SITE_KEYWORDS = "Joseph Wong, personal website, portfolio"
DEFAULT_AUTHOR = "Joseph Wong"
DEFAULT_THEME_COLOR = "#1f1f1f"

class WebPageBuilder(ABC):
	def __init__(self, template_name: str = "default.html"):
		# Flags
		self.sensitive = False   # If sensitive, we cannot serve from cache.
		self.privileged = False  # If privileged, we must authenticate the user.

		self.preload_resources: list[str] = []

		# Resources to be turned into HTML at render time
		self.scripts: set[str] = set()
		self.stylesheets: set[str] = set()
		# Don't think there's a reason not to include global to everything yet.
		self.stylesheets.add("/static/css/global.css")

		self.template_src: str = fcr.find(template_name)

		self.config_values: dict[str, str] = {}
		self.automated_fields: dict[str, dict] = {}

		self.add_default_footer_before = False
		self.add_default_footer_after = True

	def load_page_config(self, config_name: str) -> None:
		"""
		Load page configuration from JSON and populate:
		  - self.config_values
		  - self.automated_fields
		  - self.scripts / self.stylesheets
		No template substitution happens here.
		"""
		config = fcr.find(f"{config_name}.json")
		if not config:
			return

		for key, raw in (config or {}).items():
			if isinstance(raw, dict):
				if raw.get("default", False):
					continue

				if raw.get("automated", False):
					if key == "stylesheets_html":
						name_list = raw.get("name_list") or []
						self.stylesheets.update(name_list)
					elif key == "scripts_html":
						name_list = raw.get("name_list") or []
						self.scripts.update(name_list)
					else:
						self.automated_fields[key] = raw
					continue

				val = None

				if "value" in raw:
					val = raw["value"]

				elif key in ("stylesheets_html", "scripts_html", "scripts_head_html", "preconnect_html"):
					name_list = raw.get("name_list") or []

					if key == "stylesheets_html":
						self.stylesheets.update(name_list)
						continue

					if key in ("scripts_html", "scripts_head_html"):
						self.scripts.update(name_list)
						continue

					val = "\n".join(str(fcr.find(file)) for file in name_list)

				elif "name_list" in raw:
					name_list = raw.get("name_list") or []
					val = "\n".join(str(fcr.find(file)) for file in name_list)

				if val is None:
					continue

			else:
				val = "" if raw is None else str(raw)

			self.config_values[key] = "" if val is None else str(val)

	def _build_replacement_dict(self) -> dict[str, str]:
		"""
		Build the final dict of key -> string used for template substitution.
		This merges:
		  - config_values
		  - generated stylesheets_html / scripts_html from the sets
		Subclasses can modify self.config_values before calling serve_html().
		"""
		values: dict[str, str] = dict(self.config_values)

		if "stylesheets_html" not in values and self.stylesheets:
			values["stylesheets_html"] = "\n".join(
				f'<link rel="stylesheet" href="{href}">' for href in sorted(self.stylesheets)
			)

		if "scripts_html" not in values and self.scripts:
			values["scripts_html"] = "\n".join(
				f'<script src="{src}"></script>' for src in sorted(self.scripts)
			)

		return self._apply_metadata_defaults(values)

	def _first_nonempty(self, *values: str | None) -> str:
		for value in values:
			if value is None:
				continue
			value_str = str(value).strip()
			if value_str:
				return value_str
		return ""

	def _derive_meta_description(self, values: dict[str, str], max_len: int = 160) -> str:
		raw_html = values.get("body_html", "")
		if not raw_html:
			return ""
		text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", raw_html, flags=re.IGNORECASE)
		text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.IGNORECASE)
		text = re.sub(r"<[^>]+>", " ", text)
		text = html.unescape(text)
		text = re.sub(r"\s+", " ", text).strip()
		if not text:
			return ""
		if len(text) <= max_len:
			return text
		snippet = text[: max_len - 1].rstrip()
		return f"{snippet}…"

	def _apply_metadata_defaults(self, values: dict[str, str]) -> dict[str, str]:
		canonical_from_request = request.url if has_request_context() else ""
		title = self._first_nonempty(values.get("title"), DEFAULT_SITE_TITLE)
		description = self._first_nonempty(
			values.get("meta_description"),
			self._derive_meta_description(values),
			DEFAULT_SITE_DESCRIPTION,
		)
		canonical_url = self._first_nonempty(values.get("canonical_url"), canonical_from_request)
		og_image = self._first_nonempty(values.get("og_image"))

		defaults = {
			"title": title,
			"meta_description": description,
			"meta_keywords": self._first_nonempty(values.get("meta_keywords"), DEFAULT_SITE_KEYWORDS),
			"canonical_url": canonical_url,
			"robots": self._first_nonempty(values.get("robots"), "index,follow"),
			"author": self._first_nonempty(values.get("author"), DEFAULT_AUTHOR),
			"theme_color": self._first_nonempty(values.get("theme_color"), DEFAULT_THEME_COLOR),
			"og_title": self._first_nonempty(values.get("og_title"), title),
			"og_description": self._first_nonempty(values.get("og_description"), description),
			"og_url": self._first_nonempty(values.get("og_url"), canonical_url),
			"og_type": self._first_nonempty(values.get("og_type"), "website"),
			"og_image": og_image,
			"og_image_alt": self._first_nonempty(values.get("og_image_alt"), "No OG image currently set."),
			"twitter_card": self._first_nonempty(values.get("twitter_card"), "summary_large_image"),
			"twitter_site": self._first_nonempty(values.get("twitter_site")),
			"twitter_title": self._first_nonempty(values.get("twitter_title"), title),
			"twitter_description": self._first_nonempty(values.get("twitter_description"), description),
			"twitter_image": self._first_nonempty(values.get("twitter_image"), og_image),
		}

		for key, val in defaults.items():
			values[key] = val

		return values

	def _apply_values_to_template(self, tpl: str, values: dict[str, str]) -> str:
		"""
		Apply key -> value substitutions into the template string.
		Handles both:
		  - {{ key|default('...')|safe }}
		  - {{ key }}
		"""
		for key, val_str in values.items():
			pat_with_default = re.compile(
				r"\{\{\s*"
				+ re.escape(key)
				+ r"\s*\|\s*default\(\s*[^)]*?\s*\)"
				+ r"(?:\s*\|\s*safe)?\s*\}\}",
				flags=re.IGNORECASE | re.DOTALL,
			)
			tpl = pat_with_default.sub(val_str, tpl)

			pat_bare = re.compile(
				r"\{\{\s*" + re.escape(key) + r"\s*\}\}",
				flags=re.IGNORECASE,
			)
			tpl = pat_bare.sub(val_str, tpl)

		return tpl

	def serve_html(self):
		"""
		Fully compile and serve the HTML.

		Typical usage in a subclass:
			builder = LandingPageBuilder()
			builder.load_page_config("homepage")
			# optionally mutate builder.config_values, scripts, stylesheets, etc.
			return builder.serve_html()
		"""
		self._apply_default_footer()

		values = self._build_replacement_dict()

		tpl = self._apply_values_to_template(self.template_src, values)

		return render_template_string(tpl)

	def _add_banner_html(
		self,
		banner_text: list[str],
		interval: int = 6000,
		banner_type: str = "static",
	) -> None:
		"""
		banner_type: "static" | "ticker" 
		"""

		self.stylesheets.add("/static/css/alert_banner.css")

		existing = self.config_values.get("header_html", "")

		if banner_type == "static":
			self.scripts.add("/static/js/alert_banner_static.js")

			messages = "\n".join(
				f'<div class="alert-message" data-alert-message>{text}</div>'
				for text in banner_text
			)

			banner_html = f"""
		<div class="alert-banner static" data-interval="{interval}">
			{messages}
		</div>
		"""

		elif banner_type == "ticker":
			self.scripts.add("/static/js/alert_banner_ticker.js")

			ticker_items = "\n".join(
				f'<span class="alert-ticker__item">{text}</span>'
				for text in banner_text
			)

			banner_html = f"""
		<div class="alert-banner ticker" data-speed="60">
			<div class="alert-ticker">
				<div class="alert-ticker__segment" data-segment="1">
					{ticker_items}
				</div>
				<div class="alert-ticker__segment" data-segment="2">
					{ticker_items}
				</div>
			</div>
		</div>
		"""

		else:
			return

		self.config_values["header_html"] = existing + banner_html

	def _build_nav_html(self, config, user: dict | None = None, is_admin: bool = False) -> str:
		config = fcr.find(config)
		logo = config["logo"]
		items = filter_nav_items(config.get("items", []), user, is_admin)
		account_cfg = config.get("account")

		self.stylesheets.add("/static/css/navbar.css")
		self.scripts.add("/static/js/navbar.js")

		nav_items_html = []

		for item in items:
			if item["type"] == "link":
				nav_items_html.append(f"""
				<li class="nav-item">
					<a href="{item['href']}" class="nav-link">{item['label']}</a>
				</li>
				""")
			elif item["type"] == "mega":
				sections_html = []
				for section in item["sections"]:
					section_type = section.get("type")
					if section_type == "github_repos":
						username = section.get("username", "")
						limit = int(section.get("limit", 6) or 6)
						repos, total = _fetch_github_repos(username, limit=limit) if username else ([], 0)
						more_count = max(total - len(repos), 0)
						entries = [
							{
								"label": html.escape(r.get("label", "")),
								"desc": html.escape(r.get("desc", "")),
								"href": html.escape(r.get("href", "#")),
							}
							for r in repos
						]
						if more_count:
							entries.append({
								"label": html.escape(f"{more_count} more repos publicly available"),
								"desc": html.escape("View all repositories on GitHub."),
								"href": html.escape(f"https://github.com/{username}?tab=repositories"),
							})
					else:
						entries = [
							{
								"label": entry["label"],
								"desc": entry["desc"],
								"href": entry["href"],
							}
							for entry in section.get("items", [])
						]

					sec_items = "\n".join(
						f"""
						<a href="{entry['href']}" class="mega-item">
							<span class="mega-item__label">{entry['label']}</span>
							<span class="mega-item__desc">{entry['desc']}</span>
						</a>
						""" for entry in entries
					)
					sections_html.append(f"""
					<div class="mega-section">
						<h3 class="mega-section__title">{section['label']}</h3>
						<div class="mega-section__items">
							{sec_items}
						</div>
					</div>
					""")

				menu_html = "\n".join(sections_html)

				nav_items_html.append(f"""
				<li class="nav-item nav-item--has-menu" data-nav-menu>
					<button class="nav-link nav-link--trigger" type="button">
						<span>{item['label']}</span>
						<span class="nav-link__chevron" aria-hidden="true">▾</span>
					</button>
					<div class="nav-mega" aria-hidden="true">
						<div class="nav-mega__panel">
							{menu_html}
						</div>
					</div>
				</li>
				""")

		items_html = "\n".join(nav_items_html)

		account_html = ""
		account_entries: list[dict] = []
		if isinstance(account_cfg, dict):
			account_entries = [account_cfg]
		elif isinstance(account_cfg, list):
			account_entries = [a for a in account_cfg if isinstance(a, dict)]
		if user and not account_entries and isinstance(user_navbar_config.get("account"), dict):
			account_entries = [user_navbar_config["account"]]

		account_blocks: list[str] = []
		for account in account_entries:
			if not nav_entry_visible(account, user, is_admin):
				continue
			acc_type = account.get("type", "link")
			label_tpl = account.get("label", "Account")
			for key, value in (user or {}).items():
				label_tpl = label_tpl.replace(f"{{{{{key}}}}}", str(value))
			label_safe = html.escape(label_tpl)
			if acc_type == "link":
				href = html.escape(account.get("href", "#"), quote=True)
				account_blocks.append(f'<a href="{href}" class="nav-account">{label_safe}</a>')
				continue
			if acc_type != "mega":
				continue
			sections_html = []
			for section in account.get("sections", []):
				if not isinstance(section, dict):
					continue
				if not nav_entry_visible(section, user, is_admin):
					continue
				visible_entries = [
					entry for entry in section.get("items", [])
					if isinstance(entry, dict) and nav_entry_visible(entry, user, is_admin)
				]
				if not visible_entries:
					continue
				sec_items = "\n".join(
					f"""
					<a href="{html.escape(entry.get('href', '#'), quote=True)}" class="mega-item">
						<span class="mega-item__label">{html.escape(entry.get('label', ''))}</span>
						<span class="mega-item__desc">{html.escape(entry.get('desc', ''))}</span>
					</a>
					""" for entry in visible_entries
				)
				section_label = section.get("label")
				section_heading = f"<h3 class=\"mega-section__title\">{html.escape(section_label)}</h3>" if section_label else ""
				sections_html.append(f"""
				<div class="mega-section">
					{section_heading}
					<div class="mega-section__items">
						{sec_items}
					</div>
				</div>
				""")
			if not sections_html:
				continue
			menu_html = "\n".join(sections_html)
			account_blocks.append(f"""
			<div class="nav-item nav-item--has-menu" data-nav-menu>
				<button class="nav-link nav-link--trigger" type="button">
					<span>{label_safe}</span>
					<span class="nav-link__chevron" aria-hidden="true">▾</span>
				</button>
				<div class="nav-mega" aria-hidden="true">
					<div class="nav-mega__panel">
						{menu_html}
					</div>
				</div>
			</div>
			""")
		account_html = "\n".join(account_blocks)

		self.config_values["nav_html"] = self.config_values.get("nav_html", "") + f"""
		<header id="site-header" class="site-header">
			<nav class="navbar" aria-label="Primary">
				<div class="navbar__left">
					<a href="{logo['href']}" class="navbar__logo">{logo['text']}</a>
				</div>
				<div class="navbar__center">
					<ul class="nav-list">
						{items_html}
					</ul>
				</div>
				<div class="navbar__right">
					{account_html}
				</div>
			</nav>
		</header>
		"""
		return self.config_values["nav_html"]
	
	def _add_main_content_html(self, content_html: str) -> None:
		"""
		Append content to the main_content_html config value.
		"""
		existing = self.config_values.get("body_html", "")
		self.config_values["body_html"] = existing + content_html

	def _add_plotly_metric_graph(self, metric_name: str, graph_title: str = None) -> None:
		self.stylesheets.add("/static/css/plotly.css")

		self.scripts.add("https://cdn.plot.ly/plotly-2.35.2.min.js")
		self.scripts.add("/static/js/plotly.js")

		if metric_name not in METRICS_NAMES.keys():
			raise ValueError(f"Metric '{metric_name}' is not recognized.")
		
		contents = f"""
		<div class="metric-plot-container">
			<div class="metric-plot" data-metric="{metric_name}"></div>
		</div>
		"""
		self._add_main_content_html(contents)

	def _add_plotly_metric_graph_grid(
		self,
		metric_names: list[str],
		force_per_row: int | None = None,
		grid_title: str | None = None
	) -> None:
		self.stylesheets.add("/static/css/plotly.css")
		self.scripts.add("https://cdn.plot.ly/plotly-2.35.2.min.js")
		self.scripts.add("/static/js/plotly.js")

		for metric_name in metric_names:
			if metric_name not in METRICS_NAMES:
				raise ValueError(f"Metric '{metric_name}' is not recognized.")

		graph_divs = "\n".join(
			f'<div class="metric-plot" data-metric="{metric_name}"></div>'
			for metric_name in metric_names
		)

		# Optional: clamp to a sensible range if you want
		style_attr = ""
		if force_per_row is not None:
			if not isinstance(force_per_row, int) or force_per_row <= 0:
				raise ValueError("force_per_row must be a positive integer.")
			style_attr = f' style="--plots-per-row: {force_per_row};"'

		grid_wrapper = f"""
		<div class="metric-plot-grid"{style_attr}>
			{graph_divs}
		</div>
		"""

		self._add_main_content_html(grid_wrapper)

	def _add_footer_content_html(self, content_html: str) -> None:
		"""
		Append content to the footer_html config value.
		"""
		self.stylesheets.add("/static/css/footer.css")

		existing = self.config_values.get("footer_html", "")
		self.config_values["footer_html"] = existing + content_html

	def _enable_default_footer_before(self) -> None:
		self.add_default_footer_before = True
		self.add_default_footer_after = False

	def _enable_default_footer_after(self) -> None:
		self.add_default_footer_before = False
		self.add_default_footer_after = True

	def _remove_default_footer(self) -> None:
		self.add_default_footer_before = False
		self.add_default_footer_after = False

	def _apply_default_footer(self) -> None:
		if self.add_default_footer_before or self.add_default_footer_after:
			existing_footer = self.config_values.get("footer_html", "")
			self.stylesheets.add("/static/css/footer.css")

			if self.add_default_footer_before:
				footer_parts = [
					_default_footer_html,
					existing_footer,
				]
			else:
				footer_parts = [
					existing_footer,
					_default_footer_html,
				]

			self.config_values["footer_html"] = "\n".join(footer_parts)

	def _add_login_window(self) -> None:
		self.stylesheets.add("/static/css/login.css")
		# self.scripts.add("/static/js/login.js")

		existing = self.config_values.get("body_html", "")

		login_html = f"""
		<div class="login-container">
			<div class="login-window">
				<h2> Login </h2>
				{HTMLHelper.link_string("Test String", href="/")}
				{HTMLHelper.link_string("Test String", href="/login")}
				{HTMLHelper.link_string("Don't have an account? Register here.", url_for="main.register_page")}
			</div>
		</div>
		"""

		self.config_values["body_html"] = existing + login_html

	def _add_register_window(self) -> None:
		pass

	def set_page_title(self, title: str) -> None:
		self.config_values["title"] = title

# TODO: Refactor to this when implementing more graphs
class PlotlyGraph():
	def __init__(
			self, 
			initial_data: pd.DataFrame,
			update_route: str | None = None,
			if_update_keep_old: bool = False,
			title: str | None = None,
			units: str | None = None,
			layout: dict | None = None,
		):

		self.data = initial_data
		self.update_route = update_route
		self.if_update_keep_old = if_update_keep_old
		self.title = title
		self.units = units
		self.layout = layout or {}

class HTMLHelper():
	@staticmethod
	def link_string(text: str, href: str = None, url_for: str = None, class_name: str = None) -> str:
		"""
		Generate an HTML link string.
		Either href or url_for must be provided.
		"""
		if not href and not url_for:
			raise ValueError("Either href or url_for must be provided.")

		class_attr = f' class="{class_name}"' if class_name else ""

		if href:
			return f'<a href="{href}"{class_attr}>{text}</a>'
		else:
			return f'<a href="{{{{ url_for(\'{url_for}\') }}}}"{class_attr}>{text}</a>'

	@staticmethod
	def text_input(
		label: str,
		name: str,
		placeholder: str = "",
		value: str = "",
		class_name: str = "",
		prefill: str | None = None,
		input_id: str | None = None,
		input_attrs: dict[str, str] | None = None,
	):
		class_attr = f' class="{class_name}"' if class_name else ""
		prefill_attr = f' data-prefill="{prefill}"' if prefill is not None else ""
		field_id = input_id or name
		attr_str = ""
		if input_attrs:
			attr_str = "".join(
				f' {html.escape(str(k))}="{html.escape(str(v))}"'
				for k, v in input_attrs.items()
			)

		return (
			f'<label for="{field_id}">{label}</label>\n'
			f'<input '
			f'type="text" '
			f'id="{field_id}" '
			f'name="{name}" '
			f'placeholder="{placeholder}" '
			f'value="{value}"'
			f'{class_attr}'
			f'{prefill_attr}'
			f'{attr_str}'
			f'>\n'
		)
	
	@staticmethod
	def textarea_input(
		label: str,
		name: str,
		placeholder: str = "",
		value: str = "",
		class_name: str = "",
		rows: int = 6,
		prefill: str = None,
	):
		class_attr = f' class="{class_name}"' if class_name else ""
		prefill_attr = f' data-prefill="{prefill}"' if prefill is not None else ""

		return (
			f'<label for="{name}">{label}</label>\n'
			f'<textarea '
			f'id="{name}" '
			f'name="{name}" '
			f'rows="{rows}" '
			f'placeholder="{placeholder}"'
			f'{class_attr}'
			f'{prefill_attr}'
			f'>{value}</textarea>\n'
		)

	@staticmethod
	def password_input(
		label: str,
		name: str,
		placeholder: str = "",
		value: str = "",
		class_name: str = "",
		prefill: str = None,
		hide_value: bool = True,
	):
		class_attr = f' class="{class_name}"' if class_name else ""
		prefill_attr = f' data-prefill="{prefill}"' if prefill is not None else ""
		hide_attr = f' data-hide-value="{str(hide_value).lower()}"'

		return (
			f'<label for="{name}">{label}</label>\n'
			f'<input '
			f'type="password" '
			f'id="{name}" '
			f'name="{name}" '
			f'placeholder="{placeholder}" '
			f'value="{value}"'
			f'{class_attr}'
			f'{prefill_attr}'
			f'{hide_attr}'
			f'>\n'
		)

	@staticmethod
	def submit_button(
		text: str,
		submission_fields: list[str] = None,
		submission_route: str = "",
		submission_method: str = "POST",
		success_redirect: str = "",
		failure_redirect: str = "",
	):
		fields_attr = (
			f' data-submit-fields="{",".join(submission_fields)}"'
			if submission_fields
			else ""
		)

		route_attr = (
			f' data-submit-route="{submission_route}"'
			if submission_route
			else ""
		)

		method_attr = f' data-submit-method="{submission_method.upper()}"'

		success_attr = (
			f' data-success-redirect="{success_redirect}"'
			if success_redirect
			else ""
		)

		failure_attr = (
			f' data-failure-redirect="{failure_redirect}"'
			if failure_redirect
			else ""
		)

		return (
			f'<button '
			f'type="button" '
			f'{fields_attr}'
			f'{route_attr}'
			f'{method_attr}'
			f'{success_attr}'
			f'{failure_attr}'
			f'>{text}</button>\n'
		)

	@staticmethod
	def button(
		text: str,
		*,
		button_type: str = "button",
		size: str = "md",
		variant: str = "default",
		shape: str | None = None,
		class_name: str = "",
		data_attrs: dict[str, str] | None = None,
		attrs: dict[str, str] | None = None,
	) -> str:
		classes = ["btn"]
		size_map = {"xs": "btn--xs", "sm": "btn--sm", "lg": "btn--lg"}
		variant_map = {"primary": "btn--primary", "danger": "btn--danger", "ghost": "btn--ghost", "accent": "btn--accent"}
		if size in size_map:
			classes.append(size_map[size])
		if variant in variant_map:
			classes.append(variant_map[variant])
		if shape == "pill":
			classes.append("btn--pill")
		if class_name:
			classes.extend(class_name.split())

		class_attr = f' class="{" ".join(classes)}"' if classes else ""
		data_attr_str = ""
		if data_attrs:
			data_parts = [f' data-{k}="{html.escape(str(v))}"' for k, v in data_attrs.items()]
			data_attr_str = "".join(data_parts)
		attr_str = ""
		if attrs:
			attr_parts = [f' {k}="{html.escape(str(v))}"' for k, v in attrs.items()]
			attr_str = "".join(attr_parts)

		return f'<button type="{html.escape(button_type)}"{class_attr}{data_attr_str}{attr_str}>{html.escape(text)}</button>'

	@staticmethod
	def hidden_input(name: str, value: str):
		return f'<input type="hidden" name="{name}" value="{value}">\n'
	
	@staticmethod
	def checkbox_input(
		label: str,
		name: str,
		checked: bool = False,
		class_name: str = "",
	):
		class_attr = f' class="{class_name}"' if class_name else ""
		checked_attr = " checked" if checked else ""

		return (
			f'<label class="checkbox-row">'
			f'	<span class="checkbox-label">{label}</span>'
			f'	<input type="checkbox" name="{name}"{class_attr}{checked_attr}>'
			f'</label>'
		)

	@staticmethod
	def select_input(
		label: str,
		name: str,
		options: list[tuple[str, str]],
		selected: str = "",
		class_name: str = "",
	):
		class_attr = f' class="{class_name}"' if class_name else ""

		options_html = "\n".join(
			f'<option value="{value}"{" selected" if value == selected else ""}>{text}</option>'
			for value, text in options
		)

		return (
			f'<label for="{name}">{label}</label>\n'
			f'<select name="{name}" id="{name}"{class_attr}>\n'
			f'{options_html}\n'
			f'</select>\n'
		)

	@staticmethod
	def form_group(inner_html: str, class_name: str = "form-group"):
		return f'<div class="{class_name}">\n{inner_html}</div>\n'
	
	@staticmethod
	def dropdown(
		label: str,
		name: str,
		options: list[tuple[str, str]],
		selected: str = "",
		placeholder: str | None = None,
		class_name: str = "",
		required: bool = False,
	):
		class_attr = f' class="{class_name}"' if class_name else ""
		required_attr = " required" if required else ""

		options_html = []

		if placeholder is not None:
			options_html.append(
				f'<option value="" disabled{" selected" if not selected else ""}>{placeholder}</option>'
			)

		for value, text in options:
			selected_attr = " selected" if value == selected else ""
			options_html.append(
				f'<option value="{value}"{selected_attr}>{text}</option>'
			)

		return (
			f'<label for="{name}">{label}</label>\n'
			f'<select '
			f'id="{name}" '
			f'name="{name}"'
			f'{class_attr}'
			f'{required_attr}'
			f'>\n'
			f'{"".join(options_html)}\n'
			f'</select>\n'
		)
