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
	def get_logs_dir():
		src_dir = ConfigReader._get_src_base_dir()
		return os.path.join(src_dir, "logs")

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

		ext = os.path.splitext(filename)[1].lower()

		preferred_folder = None
		if ext == ".js":
			preferred_folder = "scripts"
		elif ext in (".md", ".html", ".txt"):
			preferred_folder = "content"

		if preferred_folder:
			preferred_path = os.path.join(content_dir, preferred_folder, filename)
			if os.path.exists(preferred_path):
				return preferred_path

		for root, dirs, files in os.walk(content_dir):
			if filename in files:
				return os.path.join(root, filename)

		raise FileNotFoundError(f"Content file '{filename}' not found.")
	
	@staticmethod
	def get_content_file_matches(pattern: str) -> list:
		content_dir = ConfigReader.get_content_dir()
		matches = [
			f for f in os.listdir(content_dir)
			if pattern in f
		]
		return matches

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
