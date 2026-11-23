from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from app.core.config import Settings
from app.integrations.azure_provider import AzureConfigurationError, AzureEvidenceProvider
from app.integrations.groq_cloud import ProviderUnavailableError
from app.schemas.evaluation import LLMEvaluation, SourceReferencedEvaluation


def settings(**overrides):
    values = {
        "AZURE_OPENAI_ENDPOINT": "https://synthetic-resource.openai.azure.com",
        "AZURE_OPENAI_API_KEY": "synthetic-azure-key",
        "AZURE_OPENAI_DEPLOYMENT": "synthetic-deployment",
        "AZURE_OPENAI_API_VERSION": "2024-10-21",
        "AZURE_OPENAI_MAX_RETRIES": 1,
        "AZURE_OPENAI_TIMEOUT_SECONDS": 12,
    }
    values.update(overrides)
    return Settings(**values)


def valid_output():
    return LLMEvaluation.model_validate({
        "criteria": [{
            "key": "required_skills",
            "score": 75,
            "finding": "explicit",
            "explanation": "A verified example is present.",
            "evidence": [{
                "source_text": "Built Python APIs.",
                "explanation": "Directly stated.",
                "evidence_type": "explicit",
            }],
        }],
        "missing_or_unclear_items": [],
        "recommendation_for_human_review": "Review the evidence.",
    })


def fake_client(parsed=None):
    return SimpleNamespace(
        beta=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    parse=Mock(return_value=SimpleNamespace(
                        model="synthetic-deployment",
                        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed or valid_output(), refusal=None))],
                    ))
                )
            )
        ),
        close=Mock(),
    )


def test_azure_provider_uses_deployment_and_strict_schema_without_tools():
    client = fake_client()
    provider = AzureEvidenceProvider(settings(), client=client)
    result, model = provider.generate([{"role": "system", "content": "fixed"}, {"role": "user", "content": "data"}])
    request = client.beta.chat.completions.parse.call_args.kwargs
    assert result == valid_output()
    assert model == "synthetic-deployment"
    assert request["model"] == "synthetic-deployment"
    assert request["temperature"] == 0
    assert request["response_format"] is SourceReferencedEvaluation
    assert "tools" not in request
    provider.close()
    client.close.assert_called_once()


def test_azure_provider_requires_all_config_values():
    with pytest.raises(AzureConfigurationError) as error:
        AzureEvidenceProvider(settings(AZURE_OPENAI_API_KEY=""), client=fake_client())
    assert error.value.missing_settings == ("AZURE_OPENAI_API_KEY",)
    assert "synthetic-azure-key" not in str(error.value)


@pytest.mark.parametrize("endpoint", [
    "http://synthetic-resource.openai.azure.com",
    "https://other.example.test",
    "https://user:pass@synthetic-resource.openai.azure.com",
    "https://synthetic-resource.openai.azure.com/path",
])
def test_azure_provider_rejects_non_resource_endpoints(endpoint):
    with pytest.raises(ProviderUnavailableError, match="AZURE_OPENAI_ENDPOINT"):
        AzureEvidenceProvider(settings(AZURE_OPENAI_ENDPOINT=endpoint), client=fake_client())


def test_azure_provider_retries_timeouts_with_bounded_jitter():
    client = fake_client()
    request = httpx.Request("POST", "https://synthetic-resource.openai.azure.com")
    client.beta.chat.completions.parse.side_effect = [
        APITimeoutError(request=request),
        SimpleNamespace(model="synthetic-deployment", choices=[
            SimpleNamespace(message=SimpleNamespace(parsed=valid_output(), refusal=None))]),
    ]
    delays = []
    provider = AzureEvidenceProvider(settings(), client=client, sleeper=delays.append,
                                     jitter=lambda low, high: high)
    result, _ = provider.generate([])
    assert result == valid_output()
    assert client.beta.chat.completions.parse.call_count == 2
    assert delays == [1]


def test_azure_provider_opens_circuit_after_repeated_transient_failure():
    now = [100.0]
    client = fake_client()
    request = httpx.Request("POST", "https://synthetic-resource.openai.azure.com")
    response = httpx.Response(503, request=request)
    client.beta.chat.completions.parse.side_effect = APIStatusError(
        "synthetic provider message", response=response, body={"error": "synthetic"}
    )
    provider = AzureEvidenceProvider(settings(AZURE_OPENAI_MAX_RETRIES=0), client=client,
        clock=lambda: now[0], circuit_failures=1, circuit_cooldown=10, jitter=lambda low, high: low)
    with pytest.raises(ProviderUnavailableError, match="temporarily unavailable"):
        provider.generate([])
    with pytest.raises(ProviderUnavailableError, match="circuit is open"):
        provider.generate([])
    assert client.beta.chat.completions.parse.call_count == 1
    now[0] += 11
    client.beta.chat.completions.parse.side_effect = None
    client.beta.chat.completions.parse.return_value = SimpleNamespace(
        model="synthetic-deployment",
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=valid_output(), refusal=None))],
    )
    assert provider.generate([])[0] == valid_output()
