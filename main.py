import logging
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routes.health import router as health_router
from app.routes.process_voice_note import router as voice_router
from app.routes.extract_prescriptions import router as extract_router
from app.services.whisper_service import whisper_service
from app.services.llm_service import llm_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)

def load_models():
    """Runs in a background thread so the app starts accepting requests
    (and can respond to /health) while models are loading."""
    logger.info("Starting model load sequence...")

    try:
        llm_service.load()
    except Exception as e:
        logger.error(f"LLM loading failed: {e}")

    try:
        whisper_service.load()
    except Exception as e:
        logger.error(f"Whisper loading failed: {e}")

    if whisper_service.is_ready() and llm_service.is_ready():
        logger.info("All models ready. Service is fully operational.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(target=load_models, daemon=True)
    thread.start()
    yield

app = FastAPI(
    title="CareRound AI Service",
    version="1.0.0",
    lifespan=lifespan
)

app.include_router(health_router)
app.include_router(voice_router)
app.include_router(extract_router)
