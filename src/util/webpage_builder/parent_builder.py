from abc import ABC, abstractmethod
from util.fcr.file_config_reader import FileConfigReader

class CSSManager:
    def __init__(self):
        self.styles = []

    def load_style_file(self, file_path: str):
        pass

class JSManager:
    def __init__(self):
        self.scripts = []

    def load_script_file(self, file_path: str):
        pass

class ResourceManager:
    def __init__(self):
        self.preload_resources = []

    def add_preload(self, resource: str):
        pass

class WebPageBuilder:
    def __init__(self):
        # If sensitive, we cannot serve from cache.
        self.sensitive = False
        # If privileged, we must authenticate the user.
        self.privileged = False

        self.meta_title = "No Meta Title Set"
        self.page_title = "No Title Set"
        self.preload_resources = []

    @abstractmethod
    def serve_html(self): 
        """Fully compile and serve the HTML."""
        pass

    def clear_cache(self):
        """Clear the cache for this page."""
        pass

    def set_cache(self, cache: dict):
        """Set the cache for this page."""
        pass

    def get_cache(self) -> dict:
        """Get the cache for this page."""
        pass
