import json
import logging
import re
from pathlib import Path
from typing import Any, Optional, Sequence, cast

import ollama
from pydantic import ValidationError

from app.config import settings
from app.models.schemas import LLMOutput

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "medical_system_prompt.txt"

NO_PRESCRIPTION_EXAMPLE = {
    "soap": {
        "subjective": "Patient stable.",
        "objective": "No acute findings.",
        "assessment": "Stable.",
        "plan": "Continue monitoring.",
    },
    "prescriptions": [],
}

PRESCRIPTION_EXAMPLE = {
    "soap": {
        "subjective": "Patient reports fever.",
        "objective": "Temperature elevated.",
        "assessment": "Possible infection.",
        "plan": "Start antibiotics.",
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


class LLMOutputError(RuntimeError):
    pass


def _extract_json(text: str) -> str:
    """Return raw JSON text only; markdown/code fences are schema violations."""
    stripped = text.strip()
    if re.search(r"```", stripped):
        raise LLMOutputError("LLM returned markdown/code fences instead of raw JSON")
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

        self._structure_with_repair("Patient stable. No new complaints. No medications prescribed.")
        self._ready = True
        logger.info("LLM warm-up complete; model is ready")

    def is_ready(self) -> bool:
        return self._ready

    def structure_and_extract(self, raw_text: str) -> dict:
        if not self._ready:
            raise RuntimeError("LLM not ready")

        if settings.ai_provider == "stub":
            return self._stub_structure(raw_text)

        return self._structure_with_repair(raw_text)

    def _structure_with_repair(self, raw_text: str) -> dict:
        assert self._client is not None
        first_output: dict[str, Any] = {}
        schema_errors: list[dict[str, str]] = []
        try:
            first_output = self._chat_json([
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": raw_text},
            ])
            return self._validate_exact_output(first_output)
        except json.JSONDecodeError:
            schema_errors = [{"location": "$", "error": "response must be raw valid JSON"}]
        except ValidationError as exc:
            schema_errors = self._summarize_validation_errors(exc)
        except LLMOutputError as exc:
            schema_errors = [{"location": "$", "error": str(exc)}]

        logger.warning(
            "LLM output did not match exact schema; requesting one repair attempt. errors=%s",
            schema_errors,
        )

        try:
            repaired_output = self._chat_json([
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": raw_text},
                {"role": "assistant", "content": json.dumps(first_output)},
                {
                    "role": "user",
                    "content": (
                        "Your previous response failed strict schema validation.\n"
                        f"Validation errors: {json.dumps(schema_errors)}\n"
                        "Return the same clinical content again as RAW JSON only.\n"
                        "If no medication or prescription is mentioned, prescriptions must be an empty array.\n"
                        f"No-prescription example: {json.dumps(NO_PRESCRIPTION_EXAMPLE)}\n"
                        f"Prescription example: {json.dumps(PRESCRIPTION_EXAMPLE)}\n"
                        "Rules: no markdown, no code fences, no extra keys, no missing keys, "
                        "no renamed keys, no snake_case. frequencyHours and totalDoses must be "
                        "JSON integers or null, never strings. Never return a blank or placeholder "
                        "prescription object."
                    ),
                },
            ])
            return self._validate_exact_output(repaired_output)
        except json.JSONDecodeError as exc:
            logger.error("LLM repair attempt returned malformed JSON")
            raise LLMOutputError("LLM returned JSON that does not match the required schema") from exc
        except ValidationError as exc:
            logger.error(
                "LLM failed exact schema validation after repair attempt: errors=%s",
                self._summarize_validation_errors(exc),
            )
            raise LLMOutputError("LLM returned JSON that does not match the required schema") from exc
        except LLMOutputError as exc:
            logger.error("LLM failed exact schema validation after repair attempt: %s", exc)
            raise LLMOutputError("LLM returned JSON that does not match the required schema") from exc

    def _chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        assert self._client is not None
        response = self._client.chat(
            model=settings.llm_model,
            messages=cast(Sequence[ollama.Message], messages),
            format="json",
            options={"temperature": 0.1},
        )
        content = self._extract_message_content(response)
        cleaned = _extract_json(content)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned malformed JSON: %s", type(exc).__name__)
            raise
        if not isinstance(parsed, dict):
            raise LLMOutputError("LLM returned JSON that is not an object")
        return parsed

    def _validate_exact_output(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_llm_output(data)
        return LLMOutput.model_validate(normalized).model_dump()

    def _normalize_llm_output(self, data: dict[str, Any]) -> dict[str, Any]:
        """Clean common LLM scalar mistakes without changing the response shape."""
        if not isinstance(data.get("prescriptions"), list):
            return data

        normalized = dict(data)
        prescriptions: list[Any] = []
        for item in data["prescriptions"]:
            if not isinstance(item, dict):
                prescriptions.append(item)
                continue

            prescription = dict(item)
            for key in ("dose", "route"):
                if prescription.get(key) is None:
                    logger.info("Prescription missing %s - using review placeholder", key)
                    prescription[key] = "Not specified"

            if prescription.get("frequencyString") is not None and not isinstance(prescription.get("frequencyString"), str):
                logger.info("Prescription frequencyString was not a string - clearing field")
                prescription["frequencyString"] = None

            prescriptions.append(prescription)

        normalized["prescriptions"] = prescriptions
        return normalized

    def _summarize_validation_errors(self, exc: ValidationError) -> list[dict[str, str]]:
        return [
            {
                "location": ".".join(str(part) for part in error["loc"]),
                "error": error["type"],
            }
            for error in exc.errors(include_input=False)
        ]

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
