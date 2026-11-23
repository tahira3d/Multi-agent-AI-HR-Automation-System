"""Groq wire compatibility, retry boundaries, and secret-safe failures without network calls."""
import json
from unittest.mock import patch

import httpx
import pytest
from openai import APIStatusError, APITimeoutError, OpenAI, RateLimitError

from app.agents.resume_agent import ResumeScoringAgent, SYSTEM_PROMPT
from app.core.config import Settings
from app.integrations.groq_cloud import (
    GroqConfigurationError, GroqEvidenceProvider, InvalidEvaluationError,
    ProviderAuthenticationError, ProviderRateLimitError, ProviderUnavailableError,
)
from app.schemas.evaluation import ScoringResult
from tests.test_resume_scoring import JOB, RESUME, RUBRIC, SOURCE, cfg, client_mock, completion, response


def status_error(status, headers=None):
    request = httpx.Request('POST', 'https://api.groq.com/openai/v1/chat/completions')
    result = httpx.Response(status, headers=headers, request=request)
    kind = RateLimitError if status == 429 else APIStatusError
    return kind('synthetic-private-provider-message', response=result,
                body={'error': {'message': 'synthetic-private-provider-message'}})


def test_real_sdk_groq_request_preserves_dummy_resume_scoring_contract():
    wire = response().model_dump()
    for criterion in wire['criteria']:
        for excerpt in criterion['evidence']:
            excerpt.pop('source_text')
            excerpt.update(source_line_start=1, source_line_end=1)
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == 'https://api.groq.com/openai/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer synthetic-test-key'
        body = json.loads(request.content)
        assert body['model'] == 'openai/gpt-oss-120b'
        assert body['temperature'] == 0 and body['max_tokens'] == 6000
        assert body['messages'][0] == {'role': 'system', 'content': SYSTEM_PROMPT}
        schema = body['response_format']['json_schema']
        assert body['response_format']['type'] == 'json_schema' and schema['strict'] is True
        assert schema['schema']['additionalProperties'] is False
        return httpx.Response(200, json={
            'id': 'synthetic-groq-completion', 'object': 'chat.completion', 'created': 1,
            'model': 'openai/gpt-oss-120b',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps(wire)}}],
        })

    sdk = OpenAI(base_url=cfg().groq_base_url, api_key=cfg().groq_api_key,
        max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    agent = ResumeScoringAgent(GroqEvidenceProvider(cfg(), client=sdk))
    try:
        result = agent.evaluate(resume_text=RESUME, job=JOB, rubric=RUBRIC, parsing_warnings=[])
        assert len(requests) == 1
        assert result.overall_score == 82.5
        assert result.criterion_scores == {'required_skills': 90.0, 'relevant_experience': 60.0}
        assert result.model_version == 'openai/gpt-oss-120b'
        assert result.criterion_findings == {'required_skills': 'explicit', 'relevant_experience': 'inference'}
        assert result.missing_or_unclear_items == ['Confirm employment duration.']
        assert all(item.source_text == SOURCE and RESUME[item.source_start:item.source_end] == SOURCE
                   for item in result.evidence)
        assert ScoringResult.model_validate_json(result.model_dump_json()) == result
    finally:
        agent.close()


@pytest.mark.parametrize('endpoint', [
    'http://api.groq.com/openai/v1', 'https://example.test/openai/v1',
    'https://api.groq.com/openai/v1?key=synthetic-secret',
])
def test_credentials_cannot_be_sent_to_another_endpoint(endpoint):
    with patch('app.integrations.groq_cloud.OpenAI') as sdk:
        with pytest.raises(ProviderUnavailableError, match='GROQ_BASE_URL'):
            GroqEvidenceProvider(cfg(groq_base_url=endpoint))
        sdk.assert_not_called()


def test_missing_key_does_not_fall_back_to_azure_or_openai(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', '')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', 'synthetic-private-azure-key')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-private-openai-key')
    with patch('app.integrations.groq_cloud.OpenAI') as sdk:
        with pytest.raises(GroqConfigurationError) as error:
            GroqEvidenceProvider()
        assert error.value.missing_settings == ('GROQ_API_KEY',)
        sdk.assert_not_called()


@pytest.mark.parametrize('status', [401, 403])
def test_invalid_key_or_access_fails_without_retry_or_secret_leak(status, caplog):
    client = client_mock()
    client.beta.chat.completions.parse.side_effect = status_error(status)
    sleeps = []
    provider = GroqEvidenceProvider(cfg(), client=client, sleeper=sleeps.append)
    with pytest.raises(ProviderAuthenticationError, match='GROQ_API_KEY') as error:
        provider.generate([])
    assert client.beta.chat.completions.parse.call_count == 1 and sleeps == []
    assert 'synthetic-private' not in str(error.value) + caplog.text


@pytest.mark.parametrize('retry_after,expected', [('0', 0), ('0.5', 0.5), ('8', 8),
    ('bad', 1), ('-1', 1), ('nan', 1), ('inf', 1)])
def test_rate_limit_respects_bounded_retry_after(retry_after, expected):
    client = client_mock()
    client.beta.chat.completions.parse.side_effect = [status_error(429, {'retry-after': retry_after}), completion(response())]
    sleeps = []
    provider = GroqEvidenceProvider(cfg(), client=client, sleeper=sleeps.append)
    assert provider.generate([])[0] == response()
    assert sleeps == [expected] and client.beta.chat.completions.parse.call_count == 2


def test_long_rate_limit_does_not_retry_early():
    client = client_mock()
    client.beta.chat.completions.parse.side_effect = status_error(429, {'retry-after': '60'})
    sleeps = []
    with pytest.raises(ProviderRateLimitError, match='rate limit'):
        GroqEvidenceProvider(cfg(), client=client, sleeper=sleeps.append).generate([])
    assert client.beta.chat.completions.parse.call_count == 1 and sleeps == []


@pytest.mark.parametrize('retries', [0, 2])
def test_exhausted_rate_limits_return_safe_distinct_error(retries, caplog):
    client = client_mock()
    client.beta.chat.completions.parse.side_effect = status_error(429)
    with pytest.raises(ProviderRateLimitError) as error:
        GroqEvidenceProvider(cfg(groq_max_retries=retries), client=client, sleeper=lambda _: None).generate([])
    assert client.beta.chat.completions.parse.call_count == retries + 1
    assert 'synthetic-private' not in str(error.value) + caplog.text


@pytest.mark.parametrize('status,attempts', [(400, 1), (404, 1), (422, 1), (500, 3), (503, 3), (498, 3)])
def test_api_failures_are_safe_and_retries_are_bounded(status, attempts, caplog):
    client = client_mock()
    client.beta.chat.completions.parse.side_effect = status_error(status)
    with pytest.raises(ProviderUnavailableError) as error:
        GroqEvidenceProvider(cfg(), client=client, sleeper=lambda _: None).generate([])
    assert client.beta.chat.completions.parse.call_count == attempts
    assert 'synthetic-private' not in str(error.value) + caplog.text


def test_timeout_never_produces_a_score():
    client = client_mock()
    client.beta.chat.completions.parse.side_effect = APITimeoutError(request=httpx.Request('POST', cfg().groq_base_url))
    with pytest.raises(ProviderUnavailableError, match='temporarily unavailable'):
        GroqEvidenceProvider(cfg(), client=client, sleeper=lambda _: None).generate([])
    assert client.beta.chat.completions.parse.call_count == 3


@pytest.mark.parametrize('kind', ['empty', 'refusal', 'unparsed'])
def test_unusable_evaluations_never_reach_scoring(kind):
    client = client_mock()
    value = completion(response())
    if kind == 'empty':
        value.choices = []
    elif kind == 'refusal':
        value.choices[0].message.refusal = 'synthetic refusal'
    else:
        value.choices[0].message.parsed = None
    client.beta.chat.completions.parse.return_value = value
    with pytest.raises(InvalidEvaluationError):
        GroqEvidenceProvider(cfg(), client=client, sleeper=lambda _: None).generate([])
    assert client.beta.chat.completions.parse.call_count == 3


@pytest.mark.parametrize('overrides', [
    {'groq_timeout_seconds': 0}, {'groq_timeout_seconds': 121},
    {'groq_max_retries': -1}, {'groq_max_retries': 6},
    {'groq_base_url': 'https://other.test/openai/v1'}, {'groq_model': ''},
])
def test_invalid_startup_settings_fail_safely(overrides):
    with pytest.raises(RuntimeError, match='Groq'):
        Settings(jwt_secret='x' * 40, groq_api_key='synthetic-private-key', **overrides).validate_startup()


def test_legacy_imports_preserve_exception_identity_and_provider_alias():
    from app.integrations import azure_openai as legacy
    assert legacy.AzureEvidenceProvider is GroqEvidenceProvider
    assert legacy.ProviderUnavailableError is ProviderUnavailableError
    assert legacy.InvalidEvaluationError is InvalidEvaluationError
    error = legacy.AzureConfigurationError(['GROQ_API_KEY', 'synthetic-private-value'])
    assert isinstance(error, GroqConfigurationError)
    assert error.missing_settings == ('GROQ_API_KEY',)
    assert 'synthetic-private' not in str(error)
