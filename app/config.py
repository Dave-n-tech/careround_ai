from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ai_provider: str = "ollama"
    transcription_provider: str = "whisper"
    ollama_host: str = "http://localhost:11434"
    whisper_model: str = Field(default="base", validation_alias="WHISPER_MODEL")
    llm_model: str = Field(default="mistral", validation_alias="LLM_MODEL")
    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1", validation_alias="GROQ_BASE_URL")
    groq_llm_model: str = Field(default="llama-3.3-70b-versatile", validation_alias="GROQ_LLM_MODEL")
    groq_transcription_model: str = Field(default="whisper-large-v3-turbo", validation_alias="GROQ_TRANSCRIPTION_MODEL")
    external_ai_timeout_seconds: int = Field(default=90, validation_alias="EXTERNAL_AI_TIMEOUT_SECONDS")

    class Config:
        env_file = ".env"

settings = Settings()
