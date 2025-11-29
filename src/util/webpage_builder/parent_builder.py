from abc import ABC
import re

from util.fcr.file_config_reader import FileConfigReader
from flask import render_template_string

fcr = FileConfigReader()


class WebPageBuilder(ABC):
	def __init__(self, template_name: str = "default.html"):
		# Flags
		self.sensitive = False   # If sensitive, we cannot serve from cache.
		self.privileged = False  # If privileged, we must authenticate the user.

		self.meta_title = "No Meta Title Set"
		self.page_title = "No Title Set"
		self.preload_resources: list[str] = []

		# Resources to be turned into HTML at render time
		self.scripts: set[str] = set()
		self.stylesheets: set[str] = set()

		# Raw template source (string)
		self.template_src: str = fcr.find(template_name)

		# Config-driven values that will be substituted into the template
		self.config_values: dict[str, str] = {}

		# Config entries marked "automated": True (subclass can interpret)
		self.automated_fields: dict[str, dict] = {}

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
				+ r"\s*\|\s*default\(\s*(?P<q>['\"]).*?(?P=q)\s*\)"
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

	def _add_main_content_html(self, content_html: str) -> None:
		"""
		Append content to the main_content_html config value.
		"""
		existing = self.config_values.get("body_html", "")
		self.config_values["body_html"] = existing + content_html
