from abc import ABC, abstractmethod
import re
from util.fcr.file_config_reader import FileConfigReader
from flask import render_template_string

fcr = FileConfigReader()

class WebPageBuilder:
	def __init__(self):
		# If sensitive, we cannot serve from cache.
		self.sensitive = False
		# If privileged, we must authenticate the user.
		self.privileged = False

		self.meta_title = "No Meta Title Set"
		self.page_title = "No Title Set"
		self.preload_resources = []

		self.template = fcr.find("default.html")

	@abstractmethod
	def serve_html(self): 
		"""Fully compile and serve the HTML."""
		return render_template_string(self.template)

	def load_page_config(self, config_name: str):
		"""Load page configuration from a file."""
		config = fcr.find(f"{config_name}.json")
		if config:
			self.apply_config(config)

	def apply_config(self, config: dict):
		tpl = self.template

		for key, raw in (config or {}).items():
			if isinstance(raw, dict) and "default" in raw:
				if raw.get("default", False):
					continue

				if raw.get("automated", False):
					if not hasattr(self, "automated_fields"):
						self.automated_fields = {}
					self.automated_fields[key] = raw
					continue

				if "value" in raw:
					val = raw["value"]
				elif key == "stylesheets_html":
					stylesheets = raw.get("name_list") or []
					val = "\n".join(
						f'<link rel="stylesheet" href="{file}">'
						for file in stylesheets
					)
				elif "name_list" in raw:
					name_list = raw.get("name_list") or []
					# Use fcr.find(file) for each item in the name list.
					val = "\n".join(str(fcr.find(file)) for file in name_list)
				else:
					# Nothing concrete to substitute
					continue

			else:
				val = "" if raw is None else str(raw)

			val_str = "" if val is None else str(val)

			# With-default pattern: {{ key|default('...')|safe }}
			pat_with_default = re.compile(
				r"\{\{\s*"
				+ re.escape(key)
				+ r"\s*\|\s*default\(\s*(?P<q>['\"]).*?(?P=q)\s*\)"
				+ r"(?:\s*\|\s*safe)?\s*\}\}",
				flags=re.IGNORECASE | re.DOTALL,
			)
			tpl = pat_with_default.sub(val_str, tpl)

			# Bare pattern: {{ key }}
			pat_bare = re.compile(
				r"\{\{\s*" + re.escape(key) + r"\s*\}\}",
				flags=re.IGNORECASE,
			)
			tpl = pat_bare.sub(val_str, tpl)

		self.template = tpl

