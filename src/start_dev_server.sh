#!/bin/bash
export PYTHONPATH=src
export FLASK_APP=src/run.py
export FLASK_ENV=development
flask run --port 5001
