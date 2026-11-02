from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "sqlite:///./lcms.db"
    upload_dir: str = "./storage/uploads"
    export_dir: str = "./storage/exports"
    max_upload_mb: int = 50
    llm_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    max_context_chunks: int = 6
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
