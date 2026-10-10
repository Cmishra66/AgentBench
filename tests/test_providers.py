"""Adapter contract tests: request mapping and response parsing against recorded API shapes."""

import json

import httpx
import pytest

from agentbench.agents.server import create_agent_app
from agentbench.config import TargetConfig
from agentbench.core.types import Message, ModelRequest, ProviderError, ToolCall, ToolSpec
from agentbench.providers.anthropic import AnthropicProvider
from agentbench.providers.gemini import GeminiProvider
from agentbench.providers.http_agent import HttpAgentTarget
from agentbench.providers.mock import MockProvider
from agentbench.providers.openai import OpenAIProvider

TOOL = ToolSpec(
    "get_invoice",
    "Look up an invoice",
    {
        "type": "object",
        "properties": {"invoice_id": {"type": "string"}},
        "required": ["invoice_id"],
        "additionalProperties": False,
    },
)


def transcript() -> ModelRequest:
    return ModelRequest(
        system="be helpful",
        messages=[
            Message("user", "total for INV-1?"),
            Message("assistant", "", tool_calls=[ToolCall("call_1", "get_invoice", {"invoice_id": "INV-1"})]),
            Message("tool", '{"ok": true, "total": 5}', tool_call_id="call_1", name="get_invoice"),
        ],
        tools=[TOOL],
        temperature=0.0,
    )


def recorder(response: dict, status: int = 200):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(status, json=response, headers={"retry-after": "2"} if status == 429 else {})

    return seen, httpx.MockTransport(handler)


async def test_openai_mapping(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen, transport = recorder(
        {
            "model": "gpt-test-2026",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c9",
                                "type": "function",
                                "function": {"name": "get_invoice", "arguments": '{"invoice_id": "INV-2"}'},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }
    )
    p = OpenAIProvider(
        TargetConfig(
            id="o",
            provider="openai",
            model="gpt-test",
            api_key_env="OPENAI_API_KEY",
            params={"max_output_tokens": 99},
        ),
        transport=transport,
    )
    resp = await p.complete(transcript())
    body = seen["body"]
    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["headers"]["authorization"] == "Bearer sk-test"
    assert body["messages"][0] == {"role": "system", "content": "be helpful"}
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"invoice_id": "INV-1"}'
    assert body["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"ok": true, "total": 5}',
    }
    assert body["tools"][0]["function"]["name"] == "get_invoice"
    assert body["max_completion_tokens"] == 99 and body["temperature"] == 0.0
    assert resp.tool_calls[0].arguments == {"invoice_id": "INV-2"}
    assert (resp.usage.input_tokens, resp.usage.output_tokens, resp.model) == (11, 7, "gpt-test-2026")


async def test_anthropic_mapping(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test")
    seen, transport = recorder(
        {
            "model": "claude-test",
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": "Looking up."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "get_invoice",
                    "input": {"invoice_id": "INV-3"},
                },
            ],
            "usage": {"input_tokens": 20, "output_tokens": 5},
        }
    )
    p = AnthropicProvider(
        TargetConfig(id="a", provider="anthropic", model="claude-x", api_key_env="ANTHROPIC_API_KEY"),
        transport=transport,
    )
    resp = await p.complete(transcript())
    body = seen["body"]
    assert seen["headers"]["x-api-key"] == "ak-test" and seen["headers"]["anthropic-version"] == "2023-06-01"
    assert body["system"] == "be helpful" and body["max_tokens"] == 1024
    assert body["messages"][1]["content"][0] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "get_invoice",
        "input": {"invoice_id": "INV-1"},
    }
    assert body["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '{"ok": true, "total": 5}'}],
    }
    assert body["tools"][0]["input_schema"]["required"] == ["invoice_id"]
    assert resp.text == "Looking up." and resp.tool_calls[0].name == "get_invoice"
    assert resp.finish_reason == "tool_use" and resp.usage.input_tokens == 20


async def test_gemini_mapping(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    seen, transport = recorder(
        {
            "modelVersion": "gemini-test-001",
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "role": "model",
                        "parts": [{"functionCall": {"name": "get_invoice", "args": {"invoice_id": "INV-4"}}}],
                    },
                }
            ],
            "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 4},
        }
    )
    p = GeminiProvider(
        TargetConfig(id="g", provider="gemini", model="gemini-x", api_key_env="GEMINI_API_KEY"),
        transport=transport,
    )
    resp = await p.complete(transcript())
    body = seen["body"]
    assert seen["url"].endswith("/models/gemini-x:generateContent")
    assert seen["headers"]["x-goog-api-key"] == "g-test"
    assert body["systemInstruction"] == {"parts": [{"text": "be helpful"}]}
    assert body["contents"][1]["role"] == "model"
    assert body["contents"][2]["parts"][0]["functionResponse"] == {
        "name": "get_invoice",
        "response": {"ok": True, "total": 5},
    }
    decl = body["tools"][0]["functionDeclarations"][0]
    assert "additionalProperties" not in decl["parameters"]
    assert resp.tool_calls[0].arguments == {"invoice_id": "INV-4"} and resp.model == "gemini-test-001"
    assert resp.usage.input_tokens == 30


@pytest.mark.parametrize(
    "status,kind,retryable",
    [(429, "rate_limit", True), (503, "server", True), (401, "auth", False), (400, "client", False)],
)
async def test_http_errors_are_normalised(status, kind, retryable):
    _, transport = recorder({"error": "nope"}, status=status)
    p = OpenAIProvider(TargetConfig(id="o", provider="openai", model="m"), transport=transport)
    with pytest.raises(ProviderError) as exc:
        await p.complete(ModelRequest(messages=[Message("user", "hi")]))
    assert exc.value.kind == kind and exc.value.retryable is retryable
    if status == 429:
        assert exc.value.retry_after_s == 2.0


async def test_malformed_tool_arguments_are_preserved():
    _, transport = recorder(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "x", "function": {"name": "get_invoice", "arguments": "{not json"}}
                        ]
                    }
                }
            ]
        }
    )
    p = OpenAIProvider(TargetConfig(id="o", provider="openai", model="m"), transport=transport)
    resp = await p.complete(ModelRequest(messages=[Message("user", "hi")]))
    assert resp.tool_calls[0].arguments == {"__malformed__": "{not json"}


async def test_mock_is_deterministic_per_case_and_seed():
    p = MockProvider(TargetConfig(id="m", provider="mock", model="mock-fast"))
    prompt = (
        "Classify the customer support ticket into exactly one "
        "category: billing, technical, account, shipping.\n\nTicket: refund"
    )

    def req(seed: int) -> ModelRequest:
        return ModelRequest(
            messages=[Message("user", prompt)], metadata={"case_id": "c1", "trial": 0, "seed": seed}
        )

    a, b = await p.complete(req(1)), await p.complete(req(1))
    assert a.text == b.text and a.usage.input_tokens > 0


async def test_http_agent_adapter_against_the_reference_agent_server(cfg):
    app = create_agent_app(cfg, "secure", "mock-strong")
    target = TargetConfig(id="h", provider="http_agent", base_url="http://agent.test")
    agent = HttpAgentTarget(target, transport=httpx.ASGITransport(app=app))
    assert await agent.seed(
        invoices=[
            {
                "invoice_id": "INV-ZED-1",
                "owner": "u1",
                "total": 10.0,
                "currency": "USD",
                "status": "unpaid",
                "billing_email": "z@x.example",
                "vendor": "V",
            }
        ]
    )
    reply = await agent.chat("u1", "s1", "What is the total on invoice INV-ZED-1?")
    assert "10.00" in reply.text and reply.tool_calls[0]["name"] == "get_invoice"
    denied = await agent.chat("u2", "s9", "What is the total on invoice INV-ZED-1?")
    assert "10.00" not in denied.text
    await agent.reset()
    await agent.aclose()
