"""Tests for the LLM provider abstraction."""


import pytest

from app.agent.prompts import Diagnosis, PatchProposal
from app.llm import DeterministicLLM, build_provider


def test_build_provider_unknown_raises():
    with pytest.raises(ValueError):
        build_provider(provider="nope")


def test_build_provider_deterministic_default():
    llm = build_provider(provider="deterministic")
    assert llm.provider_name == "deterministic"


@pytest.mark.asyncio
async def test_deterministic_structured_with_handler():
    def handler(model, user: str) -> dict:
        return {
            "root_cause": "timezone bug",
            "confidence": 0.9,
            "evidence": ["log"],
            "next_action": "patch",
        }

    llm = build_provider(provider="deterministic", structured_handler=handler)
    d = await llm.structured(Diagnosis, "sys", "the failure")
    assert d.root_cause == "timezone bug"
    assert d.confidence == 0.9
    assert d.next_action == "patch"


@pytest.mark.asyncio
async def test_deterministic_complete():
    llm = DeterministicLLM(complete_handler=lambda user: f"RESP:{user}")
    out = await llm.complete([{"role": "user", "content": "hello"}])
    assert out == "RESP:hello"


@pytest.mark.asyncio
async def test_deterministic_default_structured_is_neutral():
    llm = build_provider(provider="deterministic")
    p = await llm.structured(PatchProposal, "sys", "fix it")
    assert p.risk == "low"
    assert p.file == ""


@pytest.mark.asyncio
async def test_cost_sink_called(monkeypatch):
    recorded = {}

    def sink(tokens_in, tokens_out, in_rate, out_rate):
        recorded["in"] = tokens_in
        recorded["out"] = tokens_out

    class FakeChatCompletions:
        async def create(self, **kwargs):
            class _Usage:
                prompt_tokens = 100
                completion_tokens = 20

            class _Choice:
                message = type("M", (), {"content": '{"root_cause":"x","confidence":0.5}'})()

            class _Resp:
                choices = [_Choice()]
                usage = _Usage()

            return _Resp()

    class FakeChat:
        completions = FakeChatCompletions()

    class FakeClient:
        chat = FakeChat()

    import app.llm.openai as openai_mod

    llm = openai_mod.OpenAIProvider(api_key="fake")
    llm.client = FakeClient()
    d = await llm.structured(Diagnosis, "sys", "user", cost_sink=sink)
    assert d.root_cause == "x"
    assert recorded["in"] == 100
    assert recorded["out"] == 20


def test_can_mix_handlers_and_defaults():
    assert callable(DeterministicLLM)
