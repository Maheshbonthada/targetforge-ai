"""Shared LLM chat client used by every reasoning-stage agent (7-12).

One client abstraction, reused six times, rather than each agent hand-rolling
its own Azure/OpenAI wiring — the lesson carried over from TrialReady, where
the classification agent's provider-swap logic was worth centralizing.

`complete_json` is deliberately the only entry point: every reasoning agent's
LLM call is "here's context, give me back this exact typed shape," never a
free-text completion parsed with regex. JSON-mode + defensive pydantic
parsing (never trust the model's output to match schema without a fallback)
is the same pattern TrialReady's classification agent uses, generalized to
any response model instead of one hardcoded type.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel
from semantic_kernel.connectors.ai.open_ai import (
    AzureChatCompletion,
    AzureChatPromptExecutionSettings,
    OpenAIChatCompletion,
    OpenAIChatPromptExecutionSettings,
)
from semantic_kernel.contents import ChatHistory

from targetforge.config import Settings

TModel = TypeVar("TModel", bound=BaseModel)


class LlmClient(Protocol):
    async def complete_json(
        self, system_prompt: str, user_prompt: str, response_model: type[TModel], temperature: float = 0.1
    ) -> TModel: ...


def _azure_ad_token_provider() -> str:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token


class AzureOpenAILlmClient:
    def __init__(self, endpoint: str, deployment: str, api_version: str) -> None:
        self._service = AzureChatCompletion(
            deployment_name=deployment,
            endpoint=endpoint,
            api_version=api_version,
            ad_token_provider=_azure_ad_token_provider,
        )

    async def complete_json(
        self, system_prompt: str, user_prompt: str, response_model: type[TModel], temperature: float = 0.1
    ) -> TModel:
        history = ChatHistory(system_message=system_prompt)
        history.add_user_message(user_prompt)
        # semantic-kernel's PromptExecutionSettings stubs mark every field as
        # a required constructor arg even though the runtime class supplies
        # defaults for all of them (verified: the three calls this module
        # makes work fine against both real Azure OpenAI and real OpenAI) —
        # a known upstream stub gap, not a real missing-argument bug here.
        settings = AzureChatPromptExecutionSettings(  # type: ignore[call-arg]
            response_format={"type": "json_object"}, temperature=temperature, max_tokens=3000
        )
        response = await self._service.get_chat_message_content(chat_history=history, settings=settings)
        return _parse_or_raise(str(response), response_model)


class OpenAILlmClient:
    def __init__(self, api_key: str, model: str) -> None:
        self._service = OpenAIChatCompletion(ai_model_id=model, api_key=api_key)

    async def complete_json(
        self, system_prompt: str, user_prompt: str, response_model: type[TModel], temperature: float = 0.1
    ) -> TModel:
        history = ChatHistory(system_message=system_prompt)
        history.add_user_message(user_prompt)
        settings = OpenAIChatPromptExecutionSettings(  # type: ignore[call-arg]  # see AzureOpenAILlmClient above
            response_format={"type": "json_object"}, temperature=temperature, max_tokens=3000
        )
        response = await self._service.get_chat_message_content(chat_history=history, settings=settings)
        return _parse_or_raise(str(response), response_model)


class LlmParseError(RuntimeError):
    """The model's response didn't validate against the expected schema, even
    after JSON-mode. Callers should treat this as a retryable failure — see
    orchestration/supervisor.py's retry policy — never as a silent default.
    Unlike TrialReady's classification agent, there is no safe "low confidence
    fallback" value for e.g. a target ranking, so a parse failure here is a
    real failure, not a routed-to-human-review outcome.
    """


def _parse_or_raise(raw: str, response_model: type[TModel]) -> TModel:
    try:
        return response_model.model_validate_json(raw)
    except Exception as exc:
        raise LlmParseError(f"{response_model.__name__}: {exc}") from exc


def build_llm_client(settings: Settings, *, fast: bool) -> LlmClient:
    """`fast=True` selects the cheap/quick model (literature/druggability/
    competitive-intel summarization); `fast=False` selects the reasoning
    model (prioritization, report writing) — see config.py for the cost
    rationale behind routing by task instead of using one model everywhere.
    """
    if settings.ai_provider == "openai" and settings.openai_api_key:
        model = settings.openai_model_fast if fast else settings.openai_model_reasoning
        return OpenAILlmClient(api_key=settings.openai_api_key, model=model)
    deployment = settings.azure_openai_deployment_fast if fast else settings.azure_openai_deployment_reasoning
    return AzureOpenAILlmClient(
        endpoint=settings.azure_openai_endpoint,
        deployment=deployment,
        api_version=settings.azure_openai_api_version,
    )


class FakeLlmClient:
    """Test double — returns a queued canned response per call."""

    def __init__(self, responses: list[BaseModel] | None = None) -> None:
        self._responses = list(responses or [])

    async def complete_json(
        self, system_prompt: str, user_prompt: str, response_model: type[TModel], temperature: float = 0.1
    ) -> TModel:
        if not self._responses:
            raise LlmParseError("FakeLlmClient has no queued responses left")
        return self._responses.pop(0)  # type: ignore[return-value]
