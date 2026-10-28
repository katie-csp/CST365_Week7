"""Automated tests for the HealthQueue API.

All patient data here is SYNTHETIC - invented names, no real people.
Run with:  pytest -v
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app import create_app

# Synthetic test data only.
VALID_APPOINTMENT = {
    "patient_name": "Test Patient Alpha",
    "provider": "Dr. Synthetic",
    "date": "2030-01-15",
    "time": "09:30",
    "reason": "Annual checkup",
}


@pytest.fixture
def client():
    """A test client backed by a fresh app (empty storage) for each test."""
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


# ---------- Health check ----------

def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["service"] == "healthqueue-api"


# ---------- Happy paths ----------

def test_list_starts_empty(client):
    response = client.get("/appointments")
    assert response.status_code == 200
    assert response.get_json() == []


def test_create_appointment(client):
    response = client.post("/appointments", json=VALID_APPOINTMENT)
    assert response.status_code == 201
    body = response.get_json()
    assert body["id"] == 1
    assert body["patient_name"] == "Test Patient Alpha"
    assert body["status"] == "scheduled"


def test_reason_is_optional(client):
    data = {k: v for k, v in VALID_APPOINTMENT.items() if k != "reason"}
    response = client.post("/appointments", json=data)
    assert response.status_code == 201
    assert response.get_json()["reason"] == ""


def test_get_appointment_by_id(client):
    client.post("/appointments", json=VALID_APPOINTMENT)
    response = client.get("/appointments/1")
    assert response.status_code == 200
    assert response.get_json()["provider"] == "Dr. Synthetic"


def test_list_shows_created_appointments(client):
    client.post("/appointments", json=VALID_APPOINTMENT)
    client.post("/appointments", json={**VALID_APPOINTMENT, "patient_name": "Test Patient Beta"})
    response = client.get("/appointments")
    assert len(response.get_json()) == 2


def test_cancel_appointment(client):
    client.post("/appointments", json=VALID_APPOINTMENT)
    response = client.delete("/appointments/1")
    assert response.status_code == 200
    assert response.get_json()["status"] == "cancelled"


# ---------- Input validation ----------

@pytest.mark.parametrize("missing_field", ["patient_name", "provider", "date", "time"])
def test_missing_required_field(client, missing_field):
    data = {k: v for k, v in VALID_APPOINTMENT.items() if k != missing_field}
    response = client.post("/appointments", json=data)
    assert response.status_code == 400
    details = response.get_json()["error"]["details"]
    assert any(missing_field in message for message in details)


def test_blank_field_rejected(client):
    response = client.post("/appointments", json={**VALID_APPOINTMENT, "provider": "   "})
    assert response.status_code == 400


@pytest.mark.parametrize("bad_date", ["15-01-2030", "2030-13-01", "tomorrow", "2030-1-15"])
def test_invalid_date_format(client, bad_date):
    response = client.post("/appointments", json={**VALID_APPOINTMENT, "date": bad_date})
    assert response.status_code == 400
    assert "'date' must use the format YYYY-MM-DD." in response.get_json()["error"]["details"]


@pytest.mark.parametrize("bad_time", ["9:30am", "25:00", "noon", "9:30"])
def test_invalid_time_format(client, bad_time):
    response = client.post("/appointments", json={**VALID_APPOINTMENT, "time": bad_time})
    assert response.status_code == 400


def test_wrong_type_rejected(client):
    response = client.post("/appointments", json={**VALID_APPOINTMENT, "patient_name": 12345})
    assert response.status_code == 400


def test_overly_long_name_rejected(client):
    response = client.post("/appointments", json={**VALID_APPOINTMENT, "patient_name": "A" * 101})
    assert response.status_code == 400


def test_client_cannot_set_id_or_status(client):
    response = client.post("/appointments", json={**VALID_APPOINTMENT, "id": 42, "status": "cancelled"})
    body = response.get_json()
    assert body["id"] == 1
    assert body["status"] == "scheduled"


def test_concurrent_bookings_get_unique_ids():
    # Gunicorn serves requests on several threads; IDs must never collide.
    app = create_app()

    def book(_):
        with app.test_client() as c:
            return c.post("/appointments", json=VALID_APPOINTMENT).get_json()["id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(book, range(50)))
    assert sorted(ids) == list(range(1, 51))
    assert len(app.test_client().get("/appointments").get_json()) == 50


def test_non_object_json_rejected(client):
    response = client.post("/appointments", json=["not", "an", "object"])
    assert response.status_code == 400


# ---------- JSON error responses ----------

def test_invalid_json_body(client):
    response = client.post("/appointments", data="not json", content_type="application/json")
    assert response.status_code == 400
    assert response.is_json
    assert response.get_json()["error"]["message"] == "Request body must be valid JSON."


def test_get_missing_appointment_returns_json_404(client):
    response = client.get("/appointments/999")
    assert response.status_code == 404
    assert response.is_json
    assert response.get_json()["error"]["status"] == 404


def test_cancel_missing_appointment_returns_404(client):
    response = client.delete("/appointments/999")
    assert response.status_code == 404


def test_unknown_route_returns_json_404(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.is_json


def test_oversized_body_returns_json_413(client):
    huge = {**VALID_APPOINTMENT, "reason": "x" * 20_000}
    response = client.post("/appointments", json=huge)
    assert response.status_code == 413
    assert response.is_json


def test_wrong_method_returns_json_405(client):
    response = client.put("/health")
    assert response.status_code == 405
    assert response.is_json
