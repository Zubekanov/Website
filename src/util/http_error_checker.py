import re
from flask import request, abort, jsonify, make_response

def validate(request_data, required: list = None, optional: list = None):
    """
    Given request_data from a Flask request and a list of required and optional parameters,
    this function checks if all required parameters are present and returns a dictionary
    of the form:
    {
        "error": bool,          Indicates if there was an error
        "response": int,        HTTP status code
        "message": str,         Associated message if there was an error
        "request_data": dict,   Pass back the request arguments
    }
    """
    if required is None:
        required = []
    if optional is None:
        optional = []

    missing = [param for param in required if param not in request_data]
    if missing:
        return {
            "error": True,
            "response": 400,
            "message": f"Missing required parameters: {', '.join(missing)}",
            "request_data": request_data
        }

    if "email" in required:
        email_regex = r"^[\w\-\.]+@([\w\-]+\.)+[\w\-]{2,4}$"
        if not re.match(email_regex, request_data["email"]):
            return {
                "error": True,
                "response": 400,
                "message": "Invalid email format.",
                "request_data": request_data
            }
        
    # Basic password check of length >= 8 characters
    if "password" in required:
        if len(request_data["password"]) < 8:
            return {
                "error": True,
                "response": 400,
                "message": "Password must be at least 8 characters long.",
                "request_data": request_data
            }

    # Check for unexpected parameters
    unexpected = [param for param in request_data if param not in required and param not in optional]
    if unexpected:
        return {
            "error": True,
            "response": 400,
            "message": f"Unexpected parameters: {', '.join(unexpected)}",
            "request_data": request_data
        }

    return {
        "error": False,
        "response": 200,
        "message": "Success",
        "request_data": request_data
    }