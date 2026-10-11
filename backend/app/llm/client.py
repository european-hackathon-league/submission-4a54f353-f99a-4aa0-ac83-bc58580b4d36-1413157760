"""Structured LLM calls (JSON schema outputs) with retries and cost logging.

``get_llm()`` returns the OpenAI client when a key is configured. Tests and
offline runs use ``FakeLLM`` with canned handlers per prompt name.
"""

from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Protocol, TypeVar

from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.llm.prompts import Prompt

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """No OpenAI key configured; the caller must skip or use a fallback."""


@dataclass
class LLMResult(Generic[T]):
    data: T
    model: str
    prompt_version: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass
class Usage:
    calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    by_prompt: dict[str, int] = field(default_factory=dict)

    def add(self, result: LLMResult) -> None:
        self.calls += 1
        self.tokens += result.tokens_in + result.tokens_out
        self.cost_usd += result.cost_usd
        self.by_prompt[result.prompt_version] = self.by_prompt.get(result.prompt_version, 0) + 1


# Usage of the current run (seed script, agent run); copied into agent_run at the end.
current_usage: contextvars.ContextVar[Usage | None] = contextvars.ContextVar("llm_usage", default=None)


class UnsupportedSchema(ValueError):
    """The output model cannot be expressed as an OpenAI strict schema."""


_SUBSCHEMA_MAPS = ("properties", "$defs", "definitions", "patternProperties")


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema -> OpenAI strict schema (all fields required, no extras).

    Open dicts (``dict``, ``dict[str, str]``, models without fields) cannot be
    expressed in strict mode; they raise UnsupportedSchema instead of silently
    becoming an always-empty object. Use a list of key/value models instead.
    """
    schema = model.model_json_schema()

    def fix(node: Any, path: str) -> None:
        if isinstance(node, list):
            for i, value in enumerate(node):
                fix(value, f"{path}[{i}]")
            return
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        node.pop("title", None)
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties") or {}
            extra = node.get("additionalProperties")
            if not props or extra not in (None, False) or node.get("patternProperties"):
                raise UnsupportedSchema(
                    f"{model.__name__}: open object at {path or '/'} (dict or model without fields) is not"
                    " supported by OpenAI strict schemas; use a list of key/value items"
                )
            node["required"] = list(props.keys())
            node["additionalProperties"] = False
        for key, value in node.items():
            if key in _SUBSCHEMA_MAPS and isinstance(value, dict):
                # Maps of name -> schema: the names are field names, not schema keywords.
                for name, sub in value.items():
                    fix(sub, f"{path}/{key}/{name}")
            else:
                fix(value, f"{path}/{key}")

    fix(schema, "")
    return schema


class LLM(Protocol):
    def structured(
        self, prompt: Prompt, schema: type[T], model: str | None = None, **variables: Any
    ) -> LLMResult[T]: ...


def _track(result: LLMResult) -> None:
    tracker = current_usage.get()
    if tracker is not None:
        tracker.add(result)


class OpenAILLM:
    def __init__(self) -> None:
        from openai import OpenAI

        settings = get_settings()
        if not settings.openai_api_key:
            raise LLMUnavailable("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=180)
        self._settings = settings

    def structured(self, prompt: Prompt, schema: type[T], model: str | None = None, **variables: Any) -> LLMResult[T]:
        from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

        model = model or self._settings.llm_model_for(prompt.name)
        system, user = prompt.render(**variables)

        @retry(
            retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)),
            wait=wait_exponential(min=2, max=60),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        def call():
            return self._client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": strict_schema(schema), "strict": True},
                },
            )

        response = call()
        content = response.choices[0].message.content or "{}"
        data = schema.model_validate(json.loads(content))
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        cost = (tokens_in * self._settings.openai_price_in + tokens_out * self._settings.openai_price_out) / 1e6
        result = LLMResult(data, response.model, prompt.prompt_version, tokens_in, tokens_out, cost)
        _track(result)
        return result


class FakeLLM:
    """Deterministic stand-in: handlers[prompt name](variables) -> dict."""

    def __init__(self, handlers: dict[str, Callable[[dict], dict]] | None = None) -> None:
        self.handlers = handlers or {}
        self.calls: list[tuple[str, dict]] = []

    def structured(self, prompt: Prompt, schema: type[T], model: str | None = None, **variables: Any) -> LLMResult[T]:
        self.calls.append((prompt.name, variables))
        if prompt.name not in self.handlers:
            raise LLMUnavailable(f"FakeLLM has no handler for {prompt.name}")
        prompt.render(**variables)  # fail fast on missing template variables
        data = schema.model_validate(self.handlers[prompt.name](variables))
        result = LLMResult(data, "fake", prompt.prompt_version)
        _track(result)
        return result


_override: LLM | None = None


def set_llm(llm: LLM | None) -> None:
    global _override
    _override = llm


def get_llm() -> LLM:
    if _override is not None:
        return _override
    return OpenAILLM()


def llm_available() -> bool:
    return _override is not None or get_settings().has_openai
