"""Model provider abstraction. Model IDs are configuration, never code."""
from __future__ import annotations
import json
import os
from typing import Any
from . import config


class LLMClient:
    name = "base"
    is_mock = False

    def generate_json(self, model: str, prompt: str, schema: dict[str, Any],
                      images: list[tuple[bytes, str]] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def generate_text(self, model: str, prompt: str) -> str:
        raise NotImplementedError


class GeminiClient(LLMClient):
    name = "gemini"

    def __init__(self) -> None:
        from google import genai
        from google.genai import types
        self.types = types
        options = types.HttpOptions(api_version="v1", timeout=45000,
                                    retry_options=types.HttpRetryOptions(attempts=1))
        api_key = os.getenv('GEMINI_API_KEY')
        if api_key:
            self.client = genai.Client(vertexai=False, api_key=api_key, http_options=options)
        else:
            self.client = genai.Client(vertexai=True, project=config.PROJECT_ID,
                                       location=config.LOCATION, http_options=options)

    def generate_json(self, model, prompt, schema, images=None):
        parts: list[Any] = [prompt]
        for raw, mime in images or []:
            parts.append(self.types.Part.from_bytes(data=raw, mime_type=mime))
        response = self.client.models.generate_content(
            model=model, contents=parts,
            config=self.types.GenerateContentConfig(
                response_mime_type="application/json", response_json_schema=schema))
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            return parsed if isinstance(parsed, dict) else parsed.model_dump()
        return json.loads(response.text)

    def generate_text(self, model, prompt):
        return self.client.models.generate_content(model=model, contents=[prompt]).text or ""


class MockClient(LLMClient):
    """Offline mode. Capabilities detect is_mock and use their deterministic baselines."""
    name = "mock"
    is_mock = True

    def generate_json(self, model, prompt, schema, images=None):
        out: dict[str, Any] = {}
        for key, prop in schema.get("properties", {}).items():
            t = prop.get("type")
            out[key] = [] if t == "array" else False if t == "boolean" else \
                0 if t in {"integer", "number"} else ""
        return out

    def generate_text(self, model, prompt):
        return ""


_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = GeminiClient() if config.LLM_MODE == "gemini" else MockClient()
    return _llm


def set_llm(client: LLMClient | None) -> None:
    """Test hook: inject a fake client (or None to reset)."""
    global _llm
    _llm = client
