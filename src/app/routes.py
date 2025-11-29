import logging
import flask
from util.webpage_builder.webpage_builder import *

logger = logging.getLogger(__name__)
main = flask.Blueprint("main", __name__)

@main.route("/")
def landing_page():
    return build_test_page()