from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CodeSense"
    app_version: str = "0.1.0"
    debug: bool = True

    # Database — matches your local PostgreSQL 18 install
    database_url: str = "postgresql+asyncpg://postgres:codesense_dev@localhost:5432/codesense_dev"

    # CORS — configured now for future React migration
    allowed_origins: list[str] = [
        "http://localhost:5173",   # Vite (React dev)
        "http://localhost:3000",
        "http://localhost:8000",
    ]
    allowed_methods: list[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allowed_headers: list[str] = ["Authorization", "Content-Type"]

    # LLM
    groq_api_key: str = ""

settings = Settings()