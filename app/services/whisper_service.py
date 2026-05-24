import io
import logging

from app.config import settings
from app.providers.groq_provider import GroqProvider

logger = logging.getLogger(__name__)


class WhisperService:
    def __init__(self):
        self._model = None
        self._groq_provider: GroqProvider | None = None
        self._ready = False

    def load(self):
        """Call once at startup. Blocks until model is loaded."""
        provider = settings.transcription_provider.lower()
        if provider == "stub":
            logger.info("Using stub transcription provider")
            self._ready = True
            return
        if provider == "groq":
            if not settings.groq_api_key:
                raise RuntimeError("GROQ_API_KEY is required when TRANSCRIPTION_PROVIDER=groq")
            self._groq_provider = GroqProvider(
                api_key=settings.groq_api_key,
                base_url=settings.groq_base_url,
                timeout_seconds=settings.external_ai_timeout_seconds,
            )
            self._ready = True
            logger.info("Using Groq transcription provider with model: %s", settings.groq_transcription_model)
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

    def transcribe(
        self,
        audio_bytes: bytes,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        if not self._ready:
            raise RuntimeError("Whisper model not loaded")
        provider = settings.transcription_provider.lower()
        if provider == "stub":
            return "Stub transcription for local development."
        if provider == "groq":
            assert self._groq_provider is not None
            return self._groq_provider.transcribe(
                audio_bytes,
                model=settings.groq_transcription_model,
                filename=filename,
                content_type=content_type,
            )
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
