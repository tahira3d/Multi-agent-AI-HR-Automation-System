"""Local-only Phase 3 settings, independent of the working Agent 1 provider."""
from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import ROOT


def _path(name, default):
    path = Path(os.getenv(name, default))
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class VoiceSettings:
    ollama_base_url: str = field(default_factory=lambda: os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434'))
    ollama_model: str = field(default_factory=lambda: os.getenv('OLLAMA_MODEL', 'qwen2.5:1.5b'))
    ollama_timeout_seconds: float = field(default_factory=lambda: float(os.getenv('OLLAMA_TIMEOUT_SECONDS', '120')))
    whisper_model_path: Path = field(default_factory=lambda: _path('WHISPER_MODEL_PATH', 'storage/models/whisper-base'))
    whisper_device: str = field(default_factory=lambda: os.getenv('WHISPER_DEVICE', 'cpu'))
    whisper_compute_type: str = field(default_factory=lambda: os.getenv('WHISPER_COMPUTE_TYPE', 'int8'))
    piper_voice_path: Path = field(default_factory=lambda: _path('PIPER_VOICE_PATH', 'storage/models/piper/en_US-lessac-medium.onnx'))
    voice_threads: int = field(default_factory=lambda: int(os.getenv('VOICE_THREADS', '2')))
    voice_max_audio_bytes: int = field(default_factory=lambda: int(os.getenv('VOICE_MAX_AUDIO_BYTES', '5242880')))
    voice_max_audio_seconds: int = field(default_factory=lambda: int(os.getenv('VOICE_MAX_AUDIO_SECONDS', '90')))
    screening_max_followups: int = field(default_factory=lambda: int(os.getenv('SCREENING_MAX_FOLLOWUPS', '2')))
    screening_token_minutes: int = field(default_factory=lambda: int(os.getenv('SCREENING_TOKEN_MINUTES', '120')))
    screening_retention_days: int = field(default_factory=lambda: int(os.getenv('SCREENING_RETENTION_DAYS', '30')))
    screening_retention_automatic: bool = field(default_factory=lambda: os.getenv('SCREENING_RETENTION_AUTOMATIC', 'true').lower() in {'true', '1'})
    screening_default_language: str = field(default_factory=lambda: os.getenv('SCREENING_DEFAULT_LANGUAGE', 'en'))

    def validate(self):
        if not self.ollama_model.strip() or 'cloud' in self.ollama_model.casefold():
            raise ValueError('Phase 3 requires a locally installed Ollama model, not a cloud model.')
        url = urlsplit(self.ollama_base_url)
        if (url.scheme != 'http' or url.hostname not in {'localhost', '127.0.0.1', '::1'}
                or url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}):
            raise ValueError('OLLAMA_BASE_URL must point to a loopback-only local service.')
        if not 1 <= self.voice_threads <= 4 or not 1 <= self.ollama_timeout_seconds <= 180:
            raise ValueError('Local inference thread or timeout setting is outside the supported bounds.')
        if not 1 <= self.voice_max_audio_bytes <= 10485760 or not 1 <= self.voice_max_audio_seconds <= 120:
            raise ValueError('Audio size or duration setting exceeds the local resource limits.')
        if not 0 <= self.screening_max_followups <= 2 or not 1 <= self.screening_token_minutes <= 1440:
            raise ValueError('Screening follow-up or access-token limit is outside supported bounds.')
        if not 1 <= self.screening_retention_days <= 365 or self.screening_default_language not in {'en', 'ur'}:
            raise ValueError('Unsupported screening retention or default language.')
        if self.whisper_device != 'cpu' or self.whisper_compute_type != 'int8':
            raise ValueError('This local baseline supports WHISPER_DEVICE=cpu and WHISPER_COMPUTE_TYPE=int8.')
        return self


def get_voice_settings():
    return VoiceSettings().validate()
