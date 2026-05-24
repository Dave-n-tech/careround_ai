"""
Unit and integration tests for the /process-voice-note endpoint.

Services (whisper_service, llm_service) are patched per test so no real
models or Ollama connections are required.
"""

import io
import json
from unittest.mock import MagicMock, patch

from app.routes.process_voice_note import _calc_admin_times
from app.services.llm_service import LLMService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _audio():
    return {"audio": ("test.wav", io.BytesIO(b"fake-audio"), "audio/wav")}


def _empty_audio():
    return {"audio": ("recording.webm", io.BytesIO(b""), "video/webm")}


def _form(mode: str = "ward_round", current_time: str = "2025-05-19T10:00:00"):
    return {"patient_id": "p-001", "current_time": current_time, "mode": mode}


def _parse_sse(text: str) -> list[dict]:
    """Parse an SSE response body into a list of {event, data} dicts."""
    events = []
    current: dict = {}
    for line in text.split("\n"):
        if line.startswith("event: "):
            current["event"] = line[7:]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[6:])
        elif line == "" and current:
            events.append(current)
            current = {}
    return events


# ---------------------------------------------------------------------------
# Unit tests: _calc_admin_times
# ---------------------------------------------------------------------------

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


def test_llm_output_normalizes_missing_dose_and_route():
    service = LLMService()
    output = service._validate_exact_output({
        "soap": {
            "subjective": "Patient reviewed.",
            "objective": "No acute findings.",
            "assessment": "Stable.",
            "plan": "Continue care.",
        },
        "prescriptions": [
            {
                "drugName": "Ramipril",
                "dose": None,
                "route": None,
                "frequencyString": "once daily",
                "frequencyHours": 24,
                "totalDoses": None,
            }
        ],
    })

    rx = output["prescriptions"][0]
    assert rx["drugName"] == "Ramipril"
    assert rx["dose"] == "Not specified"
    assert rx["route"] == "Not specified"


# ---------------------------------------------------------------------------
# Integration tests: ward_round mode
# ---------------------------------------------------------------------------

@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_ward_round_emits_transcription_then_processing(mock_whisper, mock_llm, client):
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
    assert "text/event-stream" in resp.headers["content-type"]
    events = _parse_sse(resp.text)

    assert events[0]["event"] == "transcription_complete"
    assert events[0]["data"] == {}

    assert events[1]["event"] == "llm_structuring_complete"
    assert events[1]["data"] == {}

    assert events[2]["event"] == "processing_complete"
    pc = events[2]["data"]
    assert pc["rawTranscription"] == "Patient is stable."
    assert pc["mode"] == "ward_round"
    assert pc["clinicalNote"]["subjective"] == "Patient stable."
    assert len(pc["prescriptions"]) == 1
    rx = pc["prescriptions"][0]
    assert rx["drugName"] == "Amoxicillin"
    assert rx["administrationTimes"] == [
        "2025-05-19T10:00:00",
        "2025-05-19T16:00:00",
        "2025-05-19T22:00:00",
        "2025-05-20T04:00:00",
    ]

    assert events[3]["event"] == "done"
    assert len(events) == 4


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_prescription_missing_timing_fields_still_included(mock_whisper, mock_llm, client):
    """Prescriptions with null frequencyHours or totalDoses must still appear in the
    processing_complete event so the review UI can show them for human correction."""
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
    events = _parse_sse(resp.text)
    processing = next(e for e in events if e["event"] == "processing_complete")
    assert len(processing["data"]["prescriptions"]) == 1
    rx = processing["data"]["prescriptions"][0]
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
    events = _parse_sse(resp.text)
    processing = next(e for e in events if e["event"] == "processing_complete")
    assert processing["data"]["prescriptions"] == []


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_ward_round_rejects_llm_field_name_drift(mock_whisper, mock_llm, client):
    """Wrong key names from the LLM must surface as an error event, not a silent
    empty response or a server crash."""
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Start ceftriaxone."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.return_value = {
        "clinicalNote": {"Subjective": "Fever.", "Objective": "", "Assessment": "", "Plan": ""},
        "medications": [{"drug_name": "Ceftriaxone", "dosage": "1g"}],
    }

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[0]["event"] == "transcription_complete"
    assert events[1]["event"] == "error"
    assert "detail" in events[1]["data"]


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_ward_round_rejects_missing_required_llm_keys(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Patient reviewed."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.return_value = {
        "soap": {"subjective": "Reviewed.", "objective": "", "assessment": "", "plan": ""}
        # missing "prescriptions" key
    }

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[0]["event"] == "transcription_complete"
    assert events[1]["event"] == "error"


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_ward_round_rejects_empty_prescription_object(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Patient reviewed."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.return_value = {
        "soap": {"subjective": "Reviewed.", "objective": "", "assessment": "", "plan": ""},
        "prescriptions": [{"drugName": "", "dose": "", "route": ""}],
    }

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[0]["event"] == "transcription_complete"
    assert events[1]["event"] == "error"


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_llm_failure_emits_error_event(mock_whisper, mock_llm, client):
    """After transcription succeeds, an LLM crash must emit an error event
    rather than crashing the stream silently."""
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Patient reviewed."
    mock_llm.is_ready.return_value = True
    mock_llm.structure_and_extract.side_effect = RuntimeError("LLM crashed")

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[0]["event"] == "transcription_complete"
    assert events[1]["event"] == "error"
    assert "detail" in events[1]["data"]


# ---------------------------------------------------------------------------
# Integration tests: transcription_only mode
# ---------------------------------------------------------------------------

@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_transcription_only_emits_transcription_then_done(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.return_value = "Nurse handover note."
    mock_llm.is_ready.return_value = True

    resp = client.post("/process-voice-note", data=_form(mode="transcription_only"), files=_audio())

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[0]["event"] == "transcription_complete"
    assert events[0]["data"] == {}

    assert events[1]["event"] == "processing_complete"
    pc = events[1]["data"]
    assert pc["rawTranscription"] == "Nurse handover note."
    assert pc["mode"] == "transcription_only"
    assert pc["prescriptions"] == []
    assert "clinicalNote" not in pc

    assert events[2]["event"] == "done"
    assert len(events) == 3
    mock_llm.structure_and_extract.assert_not_called()


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_empty_audio_upload_returns_400(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_llm.is_ready.return_value = True

    resp = client.post("/process-voice-note", data=_form(), files=_empty_audio())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Uploaded audio file is empty"
    mock_whisper.transcribe.assert_not_called()


@patch("app.routes.process_voice_note.llm_service")
@patch("app.routes.process_voice_note.whisper_service")
def test_invalid_audio_emits_error_event_without_crashing(mock_whisper, mock_llm, client):
    mock_whisper.is_ready.return_value = True
    mock_whisper.transcribe.side_effect = ValueError("Invalid audio data")
    mock_llm.is_ready.return_value = True

    resp = client.post("/process-voice-note", data=_form(), files=_audio())

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events == [
        {
            "event": "error",
            "data": {"detail": "Audio could not be transcribed"},
        }
    ]
    mock_llm.structure_and_extract.assert_not_called()


# ---------------------------------------------------------------------------
# Integration tests: 503 readiness guards
# ---------------------------------------------------------------------------

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
