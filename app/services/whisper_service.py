import io
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class WhisperService:
    def __init__(self):
        self._model = None
        self._ready = False

    def load(self):
        """Call once at startup. Blocks until model is loaded."""
        if settings.transcription_provider == "stub":
            logger.info("Using stub transcription provider")
            self._ready = True
            return

        from faster_whisper import WhisperModel

        logger.info(f"Loading Whisper model: {settings.whisper_model}")
        self._model = WhisperModel(
            settings.whisper_model,
            device="cpu",  # change to "cuda" on GPU instance
            compute_type="int8",  # int8 quantisation — fast on CPU, accurate enough
        )
        self._ready = True
        logger.info("Whisper model loaded successfully")

    def is_ready(self) -> bool:
        return self._ready

    def transcribe(self, audio_bytes: bytes) -> str:
        if not self._ready:
            raise RuntimeError("Whisper model not loaded")
        if settings.transcription_provider == "stub":
            return "Stub transcription for local development."
        assert self._model is not None
        audio_file = io.BytesIO(audio_bytes)
        segments, _ = self._model.transcribe(
            audio_file,
            beam_size=5,
            language="en",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        return " ".join(segment.text.strip() for segment in segments)


whisper_service = WhisperService()
