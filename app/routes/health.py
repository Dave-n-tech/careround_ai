from fastapi import APIRouter
from app.models.schemas import HealthResponse
from app.services.whisper_service import whisper_service
from app.services.llm_service import llm_service

router = APIRouter()

@router.get("/health", response_model=HealthResponse)
async def health():
    whisper_ready = whisper_service.is_ready()
    llm_ready = llm_service.is_ready()
    status = "ready" if (whisper_ready and llm_ready) else "loading"
    return HealthResponse(
        status=status,
        whisperLoaded=whisper_ready,
        llmLoaded=llm_ready
    )