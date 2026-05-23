import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.schemas import PrescriptionExtracted
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)
router = APIRouter()


class ExtractPrescriptionsRequest(BaseModel):
    note_text: str
    patient_id: str
    current_time: str


def _calc_admin_times(current_time: str, frequency_hours: int | None, total_doses: int | None) -> list[str]:
    if frequency_hours is None or total_doses is None:
        return []
    base = datetime.fromisoformat(current_time)
    return [
        (base + timedelta(hours=i * frequency_hours)).isoformat()
        for i in range(total_doses)
    ]


@router.post("/extract-prescriptions", response_model=list[PrescriptionExtracted])
async def extract_prescriptions(body: ExtractPrescriptionsRequest):
    if not llm_service.is_ready():
        raise HTTPException(status_code=503, detail="AI service is still loading")

    logger.info("Extracting prescriptions for patient_id=%s", body.patient_id)

    try:
        raw_prescriptions = await asyncio.to_thread(llm_service.extract_prescriptions, body.note_text)
    except Exception as exc:
        logger.error(
            "Prescription extraction failed for patient_id=%s: %s",
            body.patient_id, type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Prescription extraction failed")

    result = []
    for rx in raw_prescriptions:
        try:
            admin_times = _calc_admin_times(body.current_time, rx.get("frequencyHours"), rx.get("totalDoses"))
        except Exception:
            admin_times = []
            logger.warning(
                "Admin time calculation failed for drug=%s patient_id=%s",
                rx.get("drugName"), body.patient_id,
            )
        result.append(PrescriptionExtracted(
            drugName=rx["drugName"],
            dose=rx["dose"],
            route=rx["route"],
            frequencyString=rx.get("frequencyString"),
            frequencyHours=rx.get("frequencyHours"),
            totalDoses=rx.get("totalDoses"),
            administrationTimes=admin_times,
        ))

    logger.info(
        "Extraction complete for patient_id=%s prescriptions=%d",
        body.patient_id, len(result),
    )
    return result
