"""Exercise real Gemini SDK retry behavior without contacting Google."""
import json
import httpx
import pytest
from google import genai
from google.genai import errors
from zar_core.llm import GeminiClient


def client_with_responses(monkeypatch, statuses):
    real_client = genai.Client
    requests = []
    captured = []

    def respond(request):
        requests.append(json.loads(request.content))
        status = statuses[min(len(requests) - 1, len(statuses) - 1)]
        if status != 200:
            return httpx.Response(status, json={"error": {
                "code": status, "message": "Simulated provider failure",
                "status": "UNAVAILABLE"}})
        return httpx.Response(200, json={"candidates": [{"content": {
            "role": "model", "parts": [{"text": '{"quantity":5}'}]},
            "finishReason": "STOP"}]})

    def factory(**kwargs):
        options = kwargs['http_options']
        captured.append(options.model_copy(deep=True))
        # No sleeping in tests; production settings are asserted separately.
        options.retry_options.initial_delay = 0
        options.retry_options.max_delay = 0
        options.httpx_client = httpx.Client(transport=httpx.MockTransport(respond))
        return real_client(**kwargs)

    monkeypatch.setenv('GEMINI_API_KEY', 'test-only-not-a-real-key')
    monkeypatch.setattr(genai, 'Client', factory)
    return GeminiClient(), requests, captured


@pytest.mark.parametrize('status', [429, 500, 502, 503, 504])
def test_temporary_failure_recovers_without_changing_input(monkeypatch, status):
    client, requests, options = client_with_responses(monkeypatch, [status, 200])
    try:
        result = client.generate_json('configured-model', '5 jersey for cricket, under 5K',
            {'type': 'object', 'properties': {'quantity': {'type': 'integer'}}})
        assert result == {'quantity': 5}
        assert len(requests) == 2
        assert requests[0] == requests[1]
        assert options[0].timeout == 30000
        assert options[0].retry_options.attempts == 2
        assert options[0].retry_options.max_delay == 1
    finally:
        client.client.close()


@pytest.mark.parametrize('status', [400, 401, 403, 404])
def test_permanent_failure_is_not_retried(monkeypatch, status):
    client, requests, _ = client_with_responses(monkeypatch, [status])
    try:
        with pytest.raises(errors.APIError):
            client.generate_text('configured-model', 'test')
        assert len(requests) == 1
    finally:
        client.client.close()


def test_persistent_outage_stops_after_one_retry(monkeypatch):
    client, requests, _ = client_with_responses(monkeypatch, [503])
    try:
        with pytest.raises(errors.APIError):
            client.generate_text('configured-model', 'test')
        assert len(requests) == 2
    finally:
        client.client.close()


def test_successful_text_is_not_retried(monkeypatch):
    client, requests, _ = client_with_responses(monkeypatch, [200])
    try:
        assert client.generate_text('configured-model', 'test') == '{"quantity":5}'
        assert len(requests) == 1
    finally:
        client.client.close()
