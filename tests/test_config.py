"""Environment format compatibility without exposing configuration values."""
from app.core.config import Settings, _load_dotenv, get_settings


def test_export_prefixed_groq_configuration_is_loaded(tmp_path, monkeypatch):
    values = {
        'GROQ_BASE_URL': 'https://api.groq.com/openai/v1',
        'GROQ_API_KEY': 'synthetic-test-key',
        'GROQ_MODEL': 'openai/gpt-oss-120b',
    }
    for name in values:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / '.env'
    path.write_text('\n'.join(f'export {name}="{value}"' for name, value in values.items()), encoding='utf-8')
    _load_dotenv(path)
    settings = get_settings()
    assert settings.groq_configured
    assert settings.groq_api_key == values['GROQ_API_KEY']
    assert settings.groq_model == values['GROQ_MODEL']
    assert values['GROQ_API_KEY'] not in repr(settings)


def test_export_support_preserves_process_environment_and_first_assignment(tmp_path, monkeypatch):
    monkeypatch.setenv('SYNTHETIC_EXISTING_SETTING', 'process-value')
    monkeypatch.delenv('SYNTHETIC_FIRST_SETTING', raising=False)
    path = tmp_path / '.env'
    path.write_text('export SYNTHETIC_EXISTING_SETTING=file-value\n'
                    'export\tSYNTHETIC_FIRST_SETTING=first\nSYNTHETIC_FIRST_SETTING=second\n', encoding='utf-8')
    _load_dotenv(path)
    import os
    assert os.environ['SYNTHETIC_EXISTING_SETTING'] == 'process-value'
    assert os.environ['SYNTHETIC_FIRST_SETTING'] == 'first'


def test_groq_defaults_do_not_require_azure_or_openai_credentials(monkeypatch):
    for name in ('GROQ_BASE_URL', 'GROQ_MODEL', 'GROQ_TIMEOUT_SECONDS', 'GROQ_MAX_RETRIES'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', '')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    settings = Settings()
    assert settings.groq_configured
    assert settings.groq_base_url == 'https://api.groq.com/openai/v1'
    assert settings.groq_model == 'openai/gpt-oss-120b'
    assert settings.groq_timeout_seconds == 45
    assert settings.groq_max_retries == 2


def test_production_settings_reject_wildcard_cors_and_debug():
    settings = Settings(
        ENV="production",
        JWT_SECRET_KEY="j" * 40,
        PII_ENCRYPTION_KEY="p" * 40,
        AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com",
        AZURE_OPENAI_API_KEY="synthetic-key",
        DATABASE_URL="postgresql+psycopg://user:pass@db/app",
        REDIS_URL="redis://redis:6379/0",
        CORS_ORIGINS="*",
    )
    try:
        settings.validate_startup()
    except RuntimeError as exc:
        assert "CORS_ORIGINS" in str(exc)
    else:
        raise AssertionError("Production startup accepted wildcard CORS.")


def test_production_settings_reject_short_jwt_and_missing_pii_key():
    settings = Settings(
        ENV="production",
        JWT_SECRET_KEY="too-short",
        AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com",
        AZURE_OPENAI_API_KEY="synthetic-key",
        DATABASE_URL="postgresql+psycopg://user:pass@db/app",
        REDIS_URL="redis://redis:6379/0",
    )
    try:
        settings.validate_startup()
    except RuntimeError as exc:
        assert "JWT_SECRET_KEY" in str(exc)
    else:
        raise AssertionError("Production startup accepted a short JWT secret.")


def test_production_settings_require_secrets_and_disable_debug():
    settings = Settings(
        ENV="production",
        JWT_SECRET_KEY="j" * 40,
        PII_ENCRYPTION_KEY="p" * 40,
        AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com",
        AZURE_OPENAI_API_KEY="synthetic-key",
        DATABASE_URL="postgresql+psycopg://user:pass@db/app",
        REDIS_URL="redis://redis:6379/0",
        DEBUG=True,
    )
    try:
        settings.validate_startup()
    except RuntimeError as exc:
        assert "DEBUG" in str(exc)
    else:
        raise AssertionError("Production startup accepted DEBUG=true.")


def test_secrets_are_not_in_settings_representation():
    settings = Settings(
        JWT_SECRET_KEY="synthetic-jwt-secret-value",
        PII_ENCRYPTION_KEY="synthetic-pii-encryption-value",
        AZURE_OPENAI_API_KEY="synthetic-azure-key",
    )
    rendered = repr(settings)
    assert "synthetic-jwt-secret-value" not in rendered
    assert "synthetic-pii-encryption-value" not in rendered
    assert "synthetic-azure-key" not in rendered
