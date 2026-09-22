from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    model: str = ""


class LLMProvider:
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        raise NotImplementedError


class ScriptedLLM(LLMProvider):
    """Deterministic planner for tests. Each call pops the next scripted response."""

    def __init__(self, script: list[LLMResponse]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools})
        if not self.script:
            return LLMResponse(content="No further scripted actions.")
        return self.script.pop(0)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str | None = None,
        temperature: float = 0.1,
        azure_endpoint: str | None = None,
        azure_api_version: str | None = None,
        provider: str = "openai",
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.provider = provider
        if provider == "azure":
            from openai import AzureOpenAI

            if not azure_endpoint or not api_key:
                raise RuntimeError("Azure OpenAI requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY")
            self.client = AzureOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=api_key,
                api_version=azure_api_version or "2024-08-01-preview",
            )
        else:
            from openai import OpenAI

            if provider == "ollama":
                self.client = OpenAI(
                    api_key=api_key or "ollama",
                    base_url=base_url or "http://localhost:11434/v1",
                    timeout=60.0,
                )
            elif provider == "openrouter" or (api_key or "").startswith("sk-or-"):
                if not api_key:
                    raise RuntimeError("OPENAI_API_KEY is missing for OpenRouter.")
                if "/" not in model:
                    self.model = f"openai/{model}"
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=base_url or "https://openrouter.ai/api/v1",
                    timeout=60.0,
                    default_headers={
                        "HTTP-Referer": "https://localhost",
                        "X-Title": "AI Desktop Agent",
                    },
                )
            else:
                if not api_key:
                    raise RuntimeError(
                        "OPENAI_API_KEY is missing. Copy .env.example to .env or use LLM_PROVIDER=ollama."
                    )
                kwargs: dict[str, Any] = {"api_key": api_key, "timeout": 60.0}
                if base_url:
                    kwargs["base_url"] = base_url
                self.client = OpenAI(**kwargs)

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        tool_calls: list[ToolCall] = []
        for call in message.tool_calls or []:
            tool_calls.append(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=call.function.arguments or "{}",
                )
            )
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            model=response.model or self.model,
        )


def build_provider_from_settings():
    from desktop_agent.config import get_settings

    settings = get_settings()
    return OpenAICompatibleProvider(
        model=settings.llm_model,
        api_key=settings.llm_api_key(),
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
        azure_endpoint=settings.azure_openai_endpoint,
        azure_api_version=settings.azure_openai_api_version,
        provider=settings.llm_provider.lower(),
    )
