import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.models.schemas import (
    ClinicalNote,
    LLMOutput,
    PrescriptionExtracted,
)
from app.services.llm_service import LLMOutputError, llm_service
from app.services.whisper_service import whisper_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _calc_admin_times(current_time: str, frequency_hours: int | None, total_doses: int | None) -> list[str]:
    if frequency_hours is None or total_doses is None:
        logger.info("Prescription missing timing fields - returning empty administrationTimes")
        return []
    base = datetime.fromisoformat(current_time)
    return [
        (base + timedelta(hours=i * frequency_hours)).isoformat()
        for i in range(total_doses)
    ]


def _build_prescriptions(
    llm_out: LLMOutput, current_time: str, patient_id: str
) -> list[PrescriptionExtracted]:
    out = []
    for llm_rx in llm_out.prescriptions:
        try:
            admin_times = _calc_admin_times(current_time, llm_rx.frequencyHours, llm_rx.totalDoses)
        except Exception:
            admin_times = []
            logger.warning("Admin time calculation failed for drug=%s patient_id=%s", llm_rx.drugName, patient_id)
        out.append(PrescriptionExtracted(
            drugName=llm_rx.drugName,
            dose=llm_rx.dose,
            route=llm_rx.route,
            frequencyString=llm_rx.frequencyString,
            frequencyHours=llm_rx.frequencyHours,
            totalDoses=llm_rx.totalDoses,
            administrationTimes=admin_times,
        ))
    return out


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/process-voice-note")
async def process_voice_note(
    audio: UploadFile = File(...),
    patient_id: str = Form(...),
    current_time: str = Form(...),
    mode: Literal["ward_round", "transcription_only"] = Form("ward_round"),
):
    # Readiness checked here — 503 cannot be returned once SSE headers are sent.
    if not whisper_service.is_ready():
        raise HTTPException(status_code=503, detail="Transcription model is still loading")
    if mode == "ward_round" and not llm_service.is_ready():
        raise HTTPException(status_code=503, detail="AI service is still loading")

    audio_bytes = await audio.read()
    logger.info("Received audio for patient_id=%s mode=%s bytes=%d", patient_id, mode, len(audio_bytes))

    async def event_stream() -> AsyncGenerator[str, None]:
        # asyncio.to_thread keeps the event loop free during long Whisper/LLM inference.
        try:
            transcription = await asyncio.to_thread(whisper_service.transcribe, audio_bytes)
        except Exception as exc:
            logger.error("Transcription failed for patient_id=%s: %s", patient_id, type(exc).__name__)
            yield _sse_event("error", {"detail": "Audio could not be transcribed"})
            return

        logger.info("Transcription complete for patient_id=%s length=%d", patient_id, len(transcription))
        yield _sse_event("transcription_complete", {})

        if mode == "transcription_only":
            yield _sse_event("processing_complete", {
                "rawTranscription": transcription,
                "mode": mode,
                "prescriptions": [],
            })
            yield _sse_event("done", {})
            return

        try:
            raw_llm = await asyncio.to_thread(llm_service.structure_and_extract, transcription)
            llm_out = LLMOutput.model_validate(raw_llm)
            yield _sse_event("llm_structuring_complete", {})
        except (LLMOutputError, ValidationError) as exc:
            logger.error("LLM output validation failed for patient_id=%s", patient_id)
            yield _sse_event("error", {"detail": "LLM returned unprocessable output"})
            return
        except Exception as exc:
            logger.error("LLM inference failed for patient_id=%s: %s", patient_id, type(exc).__name__)
            yield _sse_event("error", {"detail": "LLM inference failed"})
            return

        prescriptions_out = _build_prescriptions(llm_out, current_time, patient_id)
        soap = llm_out.soap
        logger.info("Extraction complete for patient_id=%s prescriptions=%d", patient_id, len(prescriptions_out))

        yield _sse_event("processing_complete", {
            "rawTranscription": transcription,
            "mode": mode,
            "clinicalNote": ClinicalNote(
                subjective=soap.subjective,
                objective=soap.objective,
                assessment=soap.assessment,
                plan=soap.plan,
            ).model_dump(),
            "prescriptions": [rx.model_dump(exclude_none=True) for rx in prescriptions_out],
        })
        yield _sse_event("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # prevent nginx from buffering SSE chunks
        },
    )
