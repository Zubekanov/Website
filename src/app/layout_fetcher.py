import os
import json
import markdown
from util.configreader import ConfigReader

class LayoutFetcher:
    @staticmethod
    def load_layout(layout_json_filename: str) -> dict:
        # Determine base content directory
        base_content_dir = ConfigReader.get_content_dir()

        # Read the JSON file
        layout_path = os.path.join(base_content_dir, layout_json_filename)
        with open(layout_path, "r") as f:
            layout_config = json.load(f)

        result = {}

        # Load navbar sections
        for section in ["navbar_left", "navbar_center", "navbar_right"]:
            file_path = os.path.join(base_content_dir, "navbar", layout_config[section])
            with open(file_path, "r") as f:
                result[section] = f.read()

        # Load page content (support multiple Markdown files)
        page_content_html = ""
        for content_file in layout_config.get("content", []):
            content_path = os.path.join(base_content_dir, "content", content_file)
            with open(content_path, "r") as f:
                md_text = f.read()
                page_content_html += markdown.markdown(md_text)
        result["page_content"] = page_content_html

        # Load page scripts
        page_scripts = []
        for script_file in layout_config.get("scripts", []):
            script_path = os.path.join(base_content_dir, "scripts", script_file)
            with open(script_path, "r") as f:
                page_scripts.append(f.read())
        result["page_scripts"] = page_scripts

        return result
