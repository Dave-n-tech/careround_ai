import json
import logging
import re
from pathlib import Path
from typing import Optional

import ollama

from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "medical_system_prompt.txt"


def _extract_json(text: str) -> str:
    """Strip markdown code fences if present, then return the raw JSON string."""
    stripped = text.strip()
    # Remove ```json ... ``` or ``` ... ``` wrappers that some LLMs add despite instructions
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if match:
        return match.group(1).strip()
    return stripped


class LLMService:
    def __init__(self):
        self._ready = False
        self._system_prompt = ""
        self._client: Optional[ollama.Client] = None

    def load(self):
        """Call once at startup. Verifies the configured LLM provider is ready."""
        self._system_prompt = PROMPT_PATH.read_text()

        if settings.ai_provider == "stub":
            logger.info("Using stub LLM provider")
            self._ready = True
            return

        self._client = ollama.Client(host=settings.ollama_host)
        logger.info("Verifying LLM model: %s at %s", settings.llm_model, settings.ollama_host)

        assert self._client is not None
        response = self._client.chat(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": "Patient stable. No new complaints."},
            ],
            format="json",
            options={"temperature": 0.1},
        )
        content = self._extract_message_content(response)
        json.loads(_extract_json(content))
        self._ready = True
        logger.info("LLM warm-up complete; model is ready")

    def is_ready(self) -> bool:
        return self._ready

    def structure_and_extract(self, raw_text: str) -> dict:
        if not self._ready:
            raise RuntimeError("LLM not ready")

        if settings.ai_provider == "stub":
            return self._stub_structure(raw_text)

        assert self._client is not None
        response = self._client.chat(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": raw_text},
            ],
            format="json",
            options={"temperature": 0.1},
        )
        content = self._extract_message_content(response)
        cleaned = _extract_json(content)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned malformed JSON: %s", type(exc).__name__)
            raise

    def _extract_message_content(self, response) -> str:
        try:
            return response["message"]["content"]
        except (KeyError, TypeError):
            pass
        try:
            items = list(response)
        except Exception as exc:
            raise RuntimeError("Unable to parse LLM response") from exc
        if not items:
            raise RuntimeError("Empty LLM response")
        last = items[-1]
        return last.get("message", {}).get("content", "")

    def _stub_structure(self, raw_text: str) -> dict:
        return {
            "soap": {
                "subjective": raw_text or "Stub subjective note.",
                "objective": "Stub objective findings.",
                "assessment": "Stub assessment.",
                "plan": "Stub plan.",
            },
            "prescriptions": [
                {
                    "drugName": "Stub Medication",
                    "dose": "500mg",
                    "route": "oral",
                    "frequencyString": "twice daily",
                    "frequencyHours": 12,
                    "totalDoses": 4,
                }
            ],
        }


llm_service = LLMService()
