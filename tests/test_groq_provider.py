from unittest.mock import Mock, patch

from app.providers.groq_provider import GroqProvider
from app.services.llm_service import LLMService


def _response(payload: dict, status_code: int = 200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = str(payload)
    return response


@patch("app.providers.groq_provider.requests.post")
def test_groq_transcription_posts_audio_multipart(mock_post):
    mock_post.return_value = _response({"text": "Patient is stable."})
    provider = GroqProvider(
        api_key="test-key",
        base_url="https://api.groq.com/openai/v1",
        timeout_seconds=90,
    )

    text = provider.transcribe(
        b"audio-bytes",
        model="whisper-large-v3-turbo",
        filename="recording.webm",
        content_type="audio/webm;codecs=opus",
    )

    assert text == "Patient is stable."
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["model"] == "whisper-large-v3-turbo"
    assert kwargs["data"]["response_format"] == "json"
    assert kwargs["files"]["file"][0] == "recording.webm"
    assert kwargs["files"]["file"][2] == "audio/webm;codecs=opus"


@patch("app.providers.groq_provider.requests.post")
def test_groq_chat_requests_json_object_mode(mock_post):
    mock_post.return_value = _response({
        "choices": [
            {
                "message": {
                    "content": '{"soap":{"subjective":"Stable","objective":"","assessment":"","plan":""},"prescriptions":[]}'
                }
            }
        ]
    })
    provider = GroqProvider(
        api_key="test-key",
        base_url="https://api.groq.com/openai/v1",
        timeout_seconds=90,
    )

    response = provider.chat_json(
        [{"role": "user", "content": "Patient stable."}],
        model="llama-3.3-70b-versatile",
    )

    assert "soap" in response["message"]["content"]
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["model"] == "llama-3.3-70b-versatile"
    assert kwargs["json"]["response_format"] == {"type": "json_object"}


@patch("app.services.llm_service.settings")
def test_llm_service_groq_provider_does_not_require_ollama_client(mock_settings):
    mock_settings.ai_provider = "groq"
    mock_settings.groq_llm_model = "llama-3.3-70b-versatile"

    service = LLMService()
    service._ready = True
    service._system_prompt = "Return JSON."
    service._groq_provider = Mock()
    service._groq_provider.chat_json.return_value = {
        "message": {
            "content": (
                '{"soap":{"subjective":"Stable","objective":"","assessment":"","plan":""},'
                '"prescriptions":[]}'
            )
        }
    }

    output = service.structure_and_extract("Patient stable.")

    assert output["soap"]["subjective"] == "Stable"
    assert output["prescriptions"] == []
    service._groq_provider.chat_json.assert_called_once()
