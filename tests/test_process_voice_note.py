"""
Unit and integration tests for the /process-voice-note endpoint.

Services (whisper_service, llm_service) are patched per test so no real
models or Ollama connections are required.
"""

import io
from unittest.mock import MagicMock, patch

from app.routes.process_voice_note import _calc_admin_times

# Helpers
def _audio():
    return {"audio": ("test.wav", io.BytesIO(b"fake-audio"), "audio/wav")}


def _form(mode: str = "ward_round", current_time: str = "2025-05-19T10:00:00"):
    return {"patient_id": "p-001", "current_time": current_time, "mode": mode}


def _ready_whisper(transcription: str = "Patient is stable.") -> MagicMock:
    svc = MagicMock()
    svc.is_ready.return_value = True
    svc.transcribe.return_value = transcription
    return svc


def _ready_llm(soap: dict | None = None, prescriptions: list | None = None) -> MagicMock:
    svc = MagicMock()
    svc.is_ready.return_value = True
    svc.structure_and_extract.return_value = {
        "soap": soap or {
            "subjective": "Patient stable.",
            "objective": "No findings.",
            "assessment": "Stable.",
            "plan": "Continue monitoring.",
        },
        "prescriptions": prescriptions if prescriptions is not None else [],
    }
    return svc


# Unit tests: _calc_admin_times
def test_calc_admin_times_basic():
    times = _calc_admin_times("2025-05-19T10:00:00", frequency_hours=6, total_doses=4)
    assert times == [
        "2025-05-19T10:00:00",
        "2025-05-19T16:00:00",
        "2025-05-19T22:00:00",
        "2025-05-20T04:00:00",
    ]


def test_calc_admin_times_single_dose():
    times = _calc_admin_times("2025-05-19T08:00:00", frequency_hours=24, total_doses=1)
    assert times == ["2025-05-19T08:00:00"]


def test_calc_admin_times_crosses_midnight():
    times = _calc_admin_times("2025-05-19T22:00:00", frequency_hours=8, total_doses=3)
    assert times == [
        "2025-05-19T22:00:00",
        "2025-05-20T06:00:00",
        "2025-05-20T14:00:00",
    ]


# Integration tests: ward_round mode
@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_ward_round_returns_full_response(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Patient is stable."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.return_value = {
        "soap": {
            "subjective": "Patient stable.",
            "objective": "No acute findings.",
            "assessment": "Stable post-op day 1.",
            "plan": "Continue IV fluids.",
        },
        "prescriptions": [
            {
                "drugName": "Amoxicillin",
                "dose": "500mg",
                "route": "oral",
                "frequencyString": "every 6 hours",
                "frequencyHours": 6,
                "totalDoses": 4,
            }
        ],
    }

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 200
    body = resp.json()
    assert body["rawTranscription"] == "Patient is stable."
    assert body["mode"] == "ward_round"
    assert body["clinicalNote"]["subjective"] == "Patient stable."
    assert len(body["prescriptions"]) == 1
    rx = body["prescriptions"][0]
    assert rx["drugName"] == "Amoxicillin"
    assert rx["administrationTimes"] == [
        "2025-05-19T10:00:00",
        "2025-05-19T16:00:00",
        "2025-05-19T22:00:00",
        "2025-05-20T04:00:00",
    ]


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_prescription_missing_timing_fields_still_included(mock_whisper, mock_llm, client):
    """Prescriptions with null frequencyHours or totalDoses must still appear in the
    response (with empty administrationTimes) so the review UI can show them."""
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Give paracetamol as needed."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.return_value = {
        "soap": {"subjective": "Pain.", "objective": "", "assessment": "", "plan": ""},
        "prescriptions": [
            {
                "drugName": "Paracetamol",
                "dose": "1g",
                "route": "oral",
                "frequencyString": "as needed",
                "frequencyHours": None,
                "totalDoses": None,
            }
        ],
    }

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["prescriptions"]) == 1
    rx = body["prescriptions"][0]
    assert rx["drugName"] == "Paracetamol"
    assert rx["administrationTimes"] == []


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_ward_round_no_prescriptions(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Patient reviewed. No new medications."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.return_value = {
        "soap": {"subjective": "Reviewed.", "objective": "", "assessment": "", "plan": ""},
        "prescriptions": [],
    }

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 200
    body = resp.json()
    assert body["prescriptions"] == []


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_ward_round_rejects_llm_field_name_drift(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Start ceftriaxone one gram IV every 12 hours for 4 doses."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.return_value = {
        "clinicalNote": {
            "Subjective": "Patient reports fever.",
            "Objective": "Temperature elevated.",
            "Assessment": "Possible infection.",
            "Plan": "Start antibiotics.",
        },
        "medications": [
            {
                "drug_name": "Ceftriaxone",
                "dosage": "1g",
                "administration_route": "IV",
                "frequency": "every 12 hours",
                "frequency_hours": "12 hours",
                "total_doses": "4",
            }
        ],
    }

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 502
    assert resp.json()["detail"] == "LLM returned unprocessable output"


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_ward_round_rejects_missing_required_llm_keys(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Patient reviewed. No new medications."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.return_value = {
        "soap": {
            "subjective": "Reviewed.",
            "objective": "",
            "assessment": "",
            "plan": "",
        }
    }

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 502
    assert resp.json()["detail"] == "LLM returned unprocessable output"


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_ward_round_rejects_empty_prescription_object(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Patient reviewed. No medications prescribed."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.return_value = {
        "soap": {
            "subjective": "Reviewed.",
            "objective": "",
            "assessment": "",
            "plan": "",
        },
        "prescriptions": [
            {
                "drugName": "",
                "dose": "",
                "route": "",
                "frequencyString": "",
                "frequencyHours": None,
                "totalDoses": None,
            }
        ],
    }

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 502
    assert resp.json()["detail"] == "LLM returned unprocessable output"


# Integration tests: transcription_only mode
@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_transcription_only_skips_llm(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Nurse handover note."
    mock_llm.is_ready.return_value = True

    resp = client.post("/process-voice-note", data=_form(mode="transcription_only"), files=_audio())

    assert resp.status_code == 200
    body = resp.json()
    assert body["rawTranscription"] == "Nurse handover note."
    assert body["mode"] == "transcription_only"
    assert body["prescriptions"] == []
    assert "clinicalNote" not in body
    mock_llm.structure_and_extract.assert_not_called()


# Integration tests: 503 readiness guards
@patch("app.routes.process_voice_note.whisper_service")
def test_503_when_whisper_not_ready(mock_whisper, client):
    mock_whisper.is_ready.return_value = False

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 503


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_503_when_llm_not_ready_for_ward_round(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_llm.is_ready.return_value = False

    resp = client.post("/process-voice-note", data=_form(mode="ward_round"), files=_audio())

    assert resp.status_code == 503


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_transcription_only_does_not_503_when_llm_not_ready(mock_whisper, mock_llm, client):
    """transcription_only only needs Whisper — LLM readiness must not block it."""
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Handover."
    mock_llm.is_ready.return_value = False

    resp = client.post("/process-voice-note", data=_form(mode="transcription_only"), files=_audio())

    assert resp.status_code == 200
