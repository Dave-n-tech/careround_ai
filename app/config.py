from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ai_provider: str = "ollama"
    transcription_provider: str = "whisper"
    ollama_host: str = "http://localhost:11434"
    whisper_model: str = Field(default="base", validation_alias="WHISPER_MODEL")
    llm_model: str = Field(default="mistral", validation_alias="LLM_MODEL")

    class Config:
        env_file = ".env"

settings = Settings()
