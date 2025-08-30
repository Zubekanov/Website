import os
from pathlib import Path
import json
import markdown
from util.config_reader import ConfigReader

content_types = (
	'.md', '.html', '.txt', '.js', '.pdf', 
)

def is_link(item: str) -> bool:
	return item.startswith("http://") or item.startswith("https://")

def is_content(item: str) -> bool:
	return item.endswith(content_types)

class LayoutFetcher:
	@staticmethod
	def load_layout(layout_json_filename: str) -> dict:
		# Namespace → Path
		base_content_dir: Path = ConfigReader().content_dir.base

		layout_path: Path = base_content_dir / layout_json_filename
		if not layout_path.exists():
			raise FileNotFoundError(f"Layout file '{layout_json_filename}' not found in {base_content_dir}")

		with layout_path.open("r", encoding="utf-8") as f:
			layout_config: dict = json.load(f)

		return LayoutFetcher.parse_iterable(layout_config)
	
	# Recursively parse the layout config.
	@staticmethod
	def parse_iterable(iterable: list | dict) -> list | dict:
		if isinstance(iterable, list):
			for index in range(len(iterable)):
				item = iterable[index]
				if isinstance(item, (list, dict)):
					iterable[index] = LayoutFetcher.parse_iterable(item)
				else:
					iterable[index] = LayoutFetcher.parse_item(item)

		if isinstance(iterable, dict):
			for key in iterable.keys():
				item = iterable[key]
				if isinstance(item, (list, dict)):
					iterable[key] = LayoutFetcher.parse_iterable(item)
				else:
					iterable[key] = LayoutFetcher.parse_item(item)
		
		return iterable

	# Root method for single items.
	@staticmethod
	def parse_item(item: str) -> str:
		if is_link(item):
			return item
		elif is_content(item) and " " not in item:
			extension = os.path.splitext(item)[-1]
			content_file = ConfigReader.get_content_file(item)
			if extension == ".md":
				with open(content_file, "r") as f:
					return markdown.markdown(f.read())
			else:
				# Currently no other type requires special handling.
				with open(content_file, "r") as f:
					return f.read()
		else:
			# Otherwise string or unhandled type.
			return item
