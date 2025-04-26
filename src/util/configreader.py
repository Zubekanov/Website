import os
import json

class ConfigReader:
	@staticmethod
	def _get_src_base_dir():
		self_file = os.path.abspath(__file__)
		util_dir = os.path.dirname(self_file)
		src_dir = os.path.dirname(util_dir)
		return src_dir

	@staticmethod
	def get_config_dir():
		src_dir = ConfigReader._get_src_base_dir()
		return os.path.join(src_dir, "config")

	@staticmethod
	def get_content_dir():
		src_dir = ConfigReader._get_src_base_dir()
		return os.path.join(src_dir, "content")

	@staticmethod
	def _resolve_filename(filename: str) -> str:
		config_dir = ConfigReader.get_config_dir()

		if "." not in filename:
			matches = [
				f for f in os.listdir(config_dir)
				if os.path.splitext(f)[0] == filename
			]
			if len(matches) == 1:
				filename = matches[0]
			elif len(matches) == 0:
				raise FileNotFoundError(f"No file found matching '{filename}'.")
			else:
				raise FileNotFoundError(f"Multiple files match '{filename}', specify extension.")

		return os.path.join(config_dir, filename)
	
	@staticmethod
	def get_content_file(filename: str) -> str:
		content_dir = ConfigReader.get_content_dir()
		content_path = os.path.join(content_dir, filename)
		if not os.path.exists(content_path):
			raise FileNotFoundError(f"Content file '{filename}' not found.")
		return content_path

	@staticmethod
	def get_raw_file(filename: str) -> str:
		config_path = ConfigReader._resolve_filename(filename)
		with open(config_path, "r", encoding="utf-8") as f:
			return f.read()

	@staticmethod
	def get_json(filename: str) -> dict:
		content = ConfigReader.get_raw_file(filename)
		return json.loads(content)

	@staticmethod
	def get_key_value_config(filename: str) -> dict:
		content = ConfigReader.get_raw_file(filename)
		result = {}

		for line in content.splitlines():
			line = line.strip()
			if not line or line.startswith("#"):
				continue
			if "=" not in line:
				raise ValueError(f"Invalid line in config: '{line}'")
			key, value = map(str.strip, line.split("=", 1))
			result[key] = value

		return result
