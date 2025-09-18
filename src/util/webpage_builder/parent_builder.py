from abc import ABC, abstractmethod
from util.fcr.file_config_reader import FileConfigReader

class CSSManager:
    def __init__(self):
        pass

class JSManager:
    def __init__(self):
        pass

class WebPageBuilder:
    def __init__(self):
        # If sensitive, we cannot serve from cache.
        self.sensitive = False
        # If privileged, we must authenticate the user.
        self.privileged = False

    @abstractmethod
    def serve_html(self): 
        """Fully compile and serve the HTML."""
        pass

    def set_cache(self, cache: dict):
        """Set the cache for this page."""
        pass

    def get_cache(self) -> dict:
        """Get the cache for this page."""
        pass
