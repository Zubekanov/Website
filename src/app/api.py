import logging
import flask

from app.api_context import ApiContext
from app.api_handlers import register_all
from app.auth_cookies import AUTH_TOKEN_NAME
from sql.psql_interface import PSQLInterface
from util.fcr.file_config_reader import FileConfigReader

logger = logging.getLogger(__name__)
api = flask.Blueprint("api", __name__)


def _build_api_context() -> ApiContext:
	return ApiContext(
		interface=PSQLInterface(),
		fcr=FileConfigReader(),
		auth_token_name=AUTH_TOKEN_NAME,
	)


_ctx = _build_api_context()
register_all(api, _ctx)
