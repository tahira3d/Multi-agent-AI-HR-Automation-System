"""Typed application settings with compatibility for the existing local deployment."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _load_dotenv(path: Path) -> None:
    """Load KEY=value and export KEY=value files without overriding process values."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith(("export ", "export\t")):
            line = line[6:].lstrip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    environment: str = Field(default="development", validation_alias="ENV")
    debug: bool = Field(default=False, validation_alias="DEBUG")
    cors_origins: str = Field(default="http://127.0.0.1:8501,http://localhost:8501",
                              validation_alias="CORS_ORIGINS")
    database_url: str = Field(default="sqlite:///./hr_hiring.sqlite3", repr=False,
                              validation_alias="DATABASE_URL")
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", repr=False, validation_alias="REDIS_URL")
    jwt_secret: str = Field(default="", repr=False,
                            validation_alias=AliasChoices("JWT_SECRET_KEY", "JWT_SECRET"))
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60, validation_alias="JWT_EXPIRE_MINUTES")
    pii_encryption_key: str = Field(default="", repr=False, validation_alias="PII_ENCRYPTION_KEY")
    storage_dir: Path = Field(default=ROOT / "storage", validation_alias=AliasChoices("STORAGE_PATH", "STORAGE_DIR"))
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, validation_alias="MAX_UPLOAD_BYTES")
    max_resume_chars: int = Field(default=120_000, validation_alias="MAX_RESUME_CHARS")
    retention_days: int = Field(default=365, validation_alias=AliasChoices("RETENTION_DAYS", "CANDIDATE_RETENTION_DAYS"))
    auto_create_tables: bool = Field(default=False, validation_alias="AUTO_CREATE_TABLES")

    azure_openai_endpoint: str = Field(default="", repr=False, validation_alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: str = Field(default="", repr=False, validation_alias="AZURE_OPENAI_API_KEY")
    azure_openai_deployment: str = Field(default="gpt-4.1-mini", validation_alias="AZURE_OPENAI_DEPLOYMENT")
    azure_openai_api_version: str = Field(default="2024-10-21", validation_alias="AZURE_OPENAI_API_VERSION")
    azure_openai_embedding_deployment: str = Field(default="", validation_alias="AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    azure_openai_timeout_seconds: float = Field(default=45, validation_alias="AZURE_OPENAI_TIMEOUT_SECONDS")
    azure_openai_max_retries: int = Field(default=2, validation_alias="AZURE_OPENAI_MAX_RETRIES")

    # Kept only for the existing phase 1/2 implementation. New AI integrations must use Azure.
    groq_base_url: str = Field(default=GROQ_BASE_URL, validation_alias="GROQ_BASE_URL")
    groq_api_key: str = Field(default="", repr=False, validation_alias="GROQ_API_KEY")
    groq_model: str = Field(default="openai/gpt-oss-120b", validation_alias="GROQ_MODEL")
    groq_timeout_seconds: float = Field(default=45, validation_alias="GROQ_TIMEOUT_SECONDS")
    groq_max_retries: int = Field(default=2, validation_alias="GROQ_MAX_RETRIES")

    checkpoint_database_url: str = Field(default="", repr=False, validation_alias="CHECKPOINT_DATABASE_URL")
    checkpoint_sqlite_path: Path = Field(default=ROOT / "storage" / "checkpoints.sqlite3",
                                         validation_alias="CHECKPOINT_SQLITE_PATH")
    telephony_provider: str = Field(default="mock", validation_alias="TELEPHONY_PROVIDER")
    fairness_mode: str = Field(default="warn", validation_alias="FAIRNESS_MODE")

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key.strip() and self.groq_base_url.rstrip("/") == GROQ_BASE_URL
                    and self.groq_model.strip())

    @property
    def azure_configured(self) -> bool:
        return bool(self.azure_openai_endpoint.strip() and self.azure_openai_api_key.strip()
                    and self.azure_openai_deployment.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def require_jwt_secret(self) -> str:
        if len(self.jwt_secret.encode("utf-8")) < 32:
            raise RuntimeError("Set JWT_SECRET_KEY to a random secret of at least 32 bytes.")
        return self.jwt_secret

    def validate_startup(self) -> None:
        self.require_jwt_secret()
        if not 1 <= self.jwt_expire_minutes <= 1440 or self.retention_days < 1:
            raise RuntimeError("Token lifetime or retention configuration is outside allowed bounds.")
        if not 1 <= self.max_upload_bytes <= 25 * 1024 * 1024:
            raise RuntimeError("MAX_UPLOAD_BYTES must be between 1 byte and 25 MiB.")
        if not 1 <= self.groq_timeout_seconds <= 120 or not 0 <= self.groq_max_retries <= 5:
            raise RuntimeError("Legacy Groq timeout or retry configuration is outside allowed bounds.")
        if not 1 <= self.azure_openai_timeout_seconds <= 120 or not 0 <= self.azure_openai_max_retries <= 5:
            raise RuntimeError("Azure timeout or retry configuration is outside allowed bounds.")
        if self.groq_api_key and not self.groq_configured:
            raise RuntimeError("Legacy Groq configuration is incomplete or invalid.")
        if self.environment.lower() == "production":
            missing = []
            if not self.pii_encryption_key:
                missing.append("PII_ENCRYPTION_KEY")
            if not self.azure_configured:
                missing.append("AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_API_KEY/AZURE_OPENAI_DEPLOYMENT")
            if not self.database_url:
                missing.append("DATABASE_URL")
            if not self.redis_url:
                missing.append("REDIS_URL")
            if missing:
                raise RuntimeError("Production configuration is missing: " + ", ".join(missing))
            if self.debug:
                raise RuntimeError("DEBUG must be false in production.")
            if "*" in self.cors_origin_list:
                raise RuntimeError("CORS_ORIGINS must not contain '*' in production.")
            if len(self.pii_encryption_key.encode("utf-8")) < 32:
                raise RuntimeError("PII_ENCRYPTION_KEY must be at least 32 bytes.")


def get_settings() -> Settings:
    return Settings()
