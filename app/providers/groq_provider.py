import io
from typing import Any

import requests


class GroqProviderError(RuntimeError):
    pass


class GroqProvider:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: int):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._headers = {
            "Authorization": f"Bearer {api_key}",
        }

    def transcribe(
        self,
        audio_bytes: bytes,
        model: str,
        filename: str | None = None,
        content_type: str | None = None,
        language: str = "en",
    ) -> str:
        files = {
            "file": (
                filename or "recording.webm",
                io.BytesIO(audio_bytes),
                content_type or "application/octet-stream",
            )
        }
        data = {
            "model": model,
            "language": language,
            "response_format": "json",
        }
        payload = self._post_multipart("/audio/transcriptions", data=data, files=files)
        text = payload.get("text")
        if not isinstance(text, str):
            raise GroqProviderError("Groq transcription response did not include text")
        return text.strip()

    def chat_json(self, messages: list[dict[str, str]], model: str) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        response_payload = self._post_json("/chat/completions", payload)
        try:
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GroqProviderError("Unable to parse Groq chat response") from exc
        if not isinstance(content, str):
            raise GroqProviderError("Groq chat response content was not a string")
        return {"message": {"content": content}}

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(
                f"{self._base_url}{path}",
                headers={**self._headers, "Content-Type": "application/json"},
                json=payload,
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise GroqProviderError("Groq request failed") from exc
        return self._decode_response(response)

    def _post_multipart(self, path: str, data: dict[str, str], files: dict[str, tuple]) -> dict[str, Any]:
        try:
            response = requests.post(
                f"{self._base_url}{path}",
                headers=self._headers,
                data=data,
                files=files,
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise GroqProviderError("Groq request failed") from exc
        return self._decode_response(response)

    def _decode_response(self, response: requests.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            detail = response.text[:500]
            raise GroqProviderError(f"Groq returned HTTP {response.status_code}: {detail}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GroqProviderError("Groq returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise GroqProviderError("Groq returned a non-object JSON response")
        return payload
