from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ai_provider: str = "ollama"
    transcription_provider: str = "whisper"
    ollama_host: str = "http://localhost:11434"
    whisper_model: str = "base"
    llm_model: str = "mistral"

    class Config:
        env_file = ".env"

settings = Settings()
