"""
Tests for POST /extract-prescriptions.

llm_service is patched per test — no real models or Ollama required.
"""

from unittest.mock import patch


def _body(note_text: str = "Prescribe Amoxicillin 500mg oral every 6 hours for 4 doses.",
          current_time: str = "2026-05-23T09:00:00"):
    return {"note_text": note_text, "patient_id": "p-001", "current_time": current_time}


@patch("app.routes.extract_prescriptions.llm_service")
def test_returns_prescriptions_with_admin_times(mock_llm, client):
    mock_llm.is_ready.return_value = True
    mock_llm.extract_prescriptions.return_value = [
        {
            "drugName": "Amoxicillin",
            "dose": "500mg",
            "route": "oral",
            "frequencyString": "every 6 hours",
            "frequencyHours": 6,
            "totalDoses": 4,
        }
    ]

    resp = client.post("/extract-prescriptions", json=_body())

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    rx = data[0]
    assert rx["drugName"] == "Amoxicillin"
    assert rx["dose"] == "500mg"
    assert rx["route"] == "oral"
    assert rx["frequencyString"] == "every 6 hours"
    assert rx["frequencyHours"] == 6
    assert rx["totalDoses"] == 4
    assert rx["administrationTimes"] == [
        "2026-05-23T09:00:00",
        "2026-05-23T15:00:00",
        "2026-05-23T21:00:00",
        "2026-05-24T03:00:00",
    ]


@patch("app.routes.extract_prescriptions.llm_service")
def test_returns_empty_array_when_no_prescriptions(mock_llm, client):
    mock_llm.is_ready.return_value = True
    mock_llm.extract_prescriptions.return_value = []

    resp = client.post("/extract-prescriptions", json=_body(note_text="Patient reviewed. No new medications."))

    assert resp.status_code == 200
    assert resp.json() == []


@patch("app.routes.extract_prescriptions.llm_service")
def test_multiple_prescriptions_each_get_admin_times(mock_llm, client):
    mock_llm.is_ready.return_value = True
    mock_llm.extract_prescriptions.return_value = [
        {
            "drugName": "Paracetamol",
            "dose": "1g",
            "route": "oral",
            "frequencyString": "every 8 hours",
            "frequencyHours": 8,
            "totalDoses": 3,
        },
        {
            "drugName": "Ibuprofen",
            "dose": "400mg",
            "route": "oral",
            "frequencyString": "as needed",
            "frequencyHours": None,
            "totalDoses": None,
        },
    ]

    resp = client.post("/extract-prescriptions", json=_body(current_time="2026-05-23T06:00:00"))

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    paracetamol = data[0]
    assert paracetamol["administrationTimes"] == [
        "2026-05-23T06:00:00",
        "2026-05-23T14:00:00",
        "2026-05-23T22:00:00",
    ]

    ibuprofen = data[1]
    assert ibuprofen["administrationTimes"] == []


@patch("app.routes.extract_prescriptions.llm_service")
def test_missing_timing_fields_yields_empty_admin_times(mock_llm, client):
    mock_llm.is_ready.return_value = True
    mock_llm.extract_prescriptions.return_value = [
        {
            "drugName": "Ramipril",
            "dose": "5mg",
            "route": "oral",
            "frequencyString": None,
            "frequencyHours": None,
            "totalDoses": None,
        }
    ]

    resp = client.post("/extract-prescriptions", json=_body())

    assert resp.status_code == 200
    rx = resp.json()[0]
    assert rx["drugName"] == "Ramipril"
    assert rx["administrationTimes"] == []


@patch("app.routes.extract_prescriptions.llm_service")
def test_503_when_llm_not_ready(mock_llm, client):
    mock_llm.is_ready.return_value = False

    resp = client.post("/extract-prescriptions", json=_body())

    assert resp.status_code == 503


@patch("app.routes.extract_prescriptions.llm_service")
def test_500_when_llm_raises(mock_llm, client):
    mock_llm.is_ready.return_value = True
    mock_llm.extract_prescriptions.side_effect = RuntimeError("connection lost")

    resp = client.post("/extract-prescriptions", json=_body())

    assert resp.status_code == 500
    assert "detail" in resp.json()
