"""HealthQueue - a simple appointment scheduling API.

Data is stored in memory, so it resets every time the app restarts.
Only synthetic (fake) data should ever be used with this app.
"""

import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

# Fields a client must send when booking an appointment.
REQUIRED_FIELDS = ["patient_name", "provider", "date", "time"]
MAX_TEXT_LENGTH = 100


def validate_appointment(data):
    """Check an appointment payload and return a list of error messages.

    An empty list means the data is valid.
    """
    if not isinstance(data, dict):
        return ["Request body must be a JSON object."]

    errors = []

    for field in REQUIRED_FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"'{field}' is required and must be a non-empty string.")
        elif len(value) > MAX_TEXT_LENGTH:
            errors.append(f"'{field}' must be {MAX_TEXT_LENGTH} characters or fewer.")

    # Only check formats if the basic type checks passed.
    date_value = data.get("date")
    if isinstance(date_value, str) and date_value.strip():
        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except ValueError:
            errors.append("'date' must use the format YYYY-MM-DD.")

    time_value = data.get("time")
    if isinstance(time_value, str) and time_value.strip():
        try:
            datetime.strptime(time_value, "%H:%M")
        except ValueError:
            errors.append("'time' must use the 24-hour format HH:MM.")

    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        errors.append("'reason' must be a string if provided.")
    elif isinstance(reason, str) and len(reason) > 250:
        errors.append("'reason' must be 250 characters or fewer.")

    return errors


def error_response(status_code, message, details=None):
    """Build a consistent JSON error body."""
    body = {"error": {"status": status_code, "message": message}}
    if details:
        body["error"]["details"] = details
    return jsonify(body), status_code


def create_app():
    """Create and configure a new Flask app.

    Using a factory means each test gets a fresh app with empty storage.
    """
    app = Flask(__name__)
    # Reject request bodies larger than 16 KB (returns a JSON 413 error).
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

    # In-memory "database": {id: appointment_dict}
    appointments = {}
    next_id = {"value": 1}

    # ---------- Health check ----------

    @app.get("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "service": "healthqueue-api",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    # ---------- Appointment endpoints ----------

    @app.get("/appointments")
    def list_appointments():
        return jsonify(list(appointments.values()))

    @app.post("/appointments")
    def create_appointment():
        # silent=True returns None instead of raising on bad JSON.
        data = request.get_json(silent=True)
        if data is None:
            return error_response(400, "Request body must be valid JSON.")

        errors = validate_appointment(data)
        if errors:
            return error_response(400, "Validation failed.", errors)

        appointment = {
            "id": next_id["value"],
            "patient_name": data["patient_name"].strip(),
            "provider": data["provider"].strip(),
            "date": data["date"],
            "time": data["time"],
            "reason": (data.get("reason") or "").strip(),
            "status": "scheduled",
        }
        appointments[appointment["id"]] = appointment
        next_id["value"] += 1
        return jsonify(appointment), 201

    @app.get("/appointments/<int:appointment_id>")
    def get_appointment(appointment_id):
        appointment = appointments.get(appointment_id)
        if appointment is None:
            return error_response(404, f"Appointment {appointment_id} not found.")
        return jsonify(appointment)

    @app.delete("/appointments/<int:appointment_id>")
    def cancel_appointment(appointment_id):
        appointment = appointments.get(appointment_id)
        if appointment is None:
            return error_response(404, f"Appointment {appointment_id} not found.")
        appointment["status"] = "cancelled"
        return jsonify(appointment)

    # ---------- JSON error handlers ----------

    @app.errorhandler(HTTPException)
    def handle_http_error(err):
        # Covers 404 (unknown URL), 405 (wrong method), etc.
        return error_response(err.code, err.description)

    @app.errorhandler(Exception)
    def handle_unexpected_error(err):
        app.logger.exception("Unexpected error")
        return error_response(500, "Internal server error.")

    return app


if __name__ == "__main__":
    # Local development server only (the container uses Gunicorn).
    # Debug mode enables an interactive debugger that can run code, so it is
    # OFF unless explicitly requested with:  $env:FLASK_DEBUG = "1"
    debug = os.environ.get("FLASK_DEBUG") == "1"
    create_app().run(host="127.0.0.1", port=5000, debug=debug)
