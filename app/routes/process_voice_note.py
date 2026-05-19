from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.models.schemas import ClinicalNote, ProcessVoiceNoteResponse
from app.services.llm_service import llm_service
from app.services.whisper_service import whisper_service

router = APIRouter()


@router.post(
    "/process-voice-note",
    response_model=ProcessVoiceNoteResponse,
    response_model_exclude_none=True,
)
async def process_voice_note(
    audio: UploadFile = File(...),
    patient_id: str = Form(...),
    current_time: str = Form(...),
    mode: Literal["ward_round", "transcription_only"] = Form("ward_round"),
):
    # Full implementation comes after deployment is verified.
    # Returning stub responses to confirm the endpoint contract is wired.
    if mode == "transcription_only":
        if not whisper_service.is_ready():
            raise HTTPException(status_code=503, detail="Transcription model is still loading")
        return ProcessVoiceNoteResponse(
            rawTranscription="[stub - real transcription pending]",
            mode=mode,
            prescriptions=[],
        )

    if not whisper_service.is_ready() or not llm_service.is_ready():
        raise HTTPException(status_code=503, detail="AI service is still loading")

    return ProcessVoiceNoteResponse(
        rawTranscription="[stub - real implementation pending]",
        mode=mode,
        clinicalNote=ClinicalNote(
            subjective="stub",
            objective="stub",
            assessment="stub",
            plan="stub",
        ),
        prescriptions=[],
    )
