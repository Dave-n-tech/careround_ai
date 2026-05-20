import logging
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from app.models.schemas import (
    ClinicalNote,
    LLMOutput,
    PrescriptionExtracted,
    ProcessVoiceNoteResponse,
)
from app.services.llm_service import llm_service
from app.services.whisper_service import whisper_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _calc_admin_times(current_time: str, frequency_hours: int, total_doses: int) -> list[str]:
    base = datetime.fromisoformat(current_time)
    return [
        (base + timedelta(hours=i * frequency_hours)).isoformat()
        for i in range(total_doses)
    ]


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
    if not whisper_service.is_ready():
        raise HTTPException(status_code=503, detail="Transcription model is still loading")
    if mode == "ward_round" and not llm_service.is_ready():
        raise HTTPException(status_code=503, detail="AI service is still loading")

    audio_bytes = await audio.read()
    logger.info("Received audio for patient_id=%s mode=%s bytes=%d", patient_id, mode, len(audio_bytes))

    transcription = whisper_service.transcribe(audio_bytes)
    logger.info("Transcription complete for patient_id=%s length=%d", patient_id, len(transcription))

    if mode == "transcription_only":
        return ProcessVoiceNoteResponse(
            rawTranscription=transcription,
            mode=mode,
            prescriptions=[],
        )

    # ward_round: structure note and extract prescriptions via LLM
    try:
        raw_llm = llm_service.structure_and_extract(transcription)
    except Exception as exc:
        logger.error("LLM inference failed for patient_id=%s: %s", patient_id, type(exc).__name__)
        raise HTTPException(status_code=502, detail="LLM inference failed") from exc

    try:
        llm_out = LLMOutput.model_validate(raw_llm)
    except ValidationError as exc:
        logger.error("LLM output validation failed for patient_id=%s: %s", patient_id, exc)
        raise HTTPException(status_code=502, detail="LLM returned unprocessable output") from exc

    prescriptions_out: list[PrescriptionExtracted] = []
    for llm_rx in llm_out.prescriptions:
        admin_times: list[str] = []
        if llm_rx.frequencyHours is not None and llm_rx.totalDoses is not None:
            try:
                admin_times = _calc_admin_times(current_time, llm_rx.frequencyHours, llm_rx.totalDoses)
            except Exception:
                logger.warning("Admin time calculation failed for drug=%s patient_id=%s", llm_rx.drugName, patient_id)

        prescriptions_out.append(PrescriptionExtracted(
            drugName=llm_rx.drugName,
            dose=llm_rx.dose,
            route=llm_rx.route,
            frequencyString=llm_rx.frequencyString,
            frequencyHours=llm_rx.frequencyHours,
            totalDoses=llm_rx.totalDoses,
            administrationTimes=admin_times,
        ))

    logger.info(
        "Extraction complete for patient_id=%s prescriptions=%d",
        patient_id,
        len(prescriptions_out),
    )

    soap = llm_out.soap
    return ProcessVoiceNoteResponse(
        rawTranscription=transcription,
        mode=mode,
        clinicalNote=ClinicalNote(
            subjective=soap.subjective,
            objective=soap.objective,
            assessment=soap.assessment,
            plan=soap.plan,
        ),
        prescriptions=prescriptions_out,
    )
