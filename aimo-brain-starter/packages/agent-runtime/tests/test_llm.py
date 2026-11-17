import pytest
from agent_runtime.llm import FakeLLM, LLMMessage, get_llm_adapter


@pytest.mark.asyncio
async def test_fake_llm_is_deterministic() -> None:
    llm = FakeLLM()
    messages = [LLMMessage(role="user", content="quote 100L lactose-free milk")]
    first = await llm.complete(messages)
    second = await llm.complete(messages)
    assert first.content == second.content
    assert first.deterministic is True


@pytest.mark.asyncio
async def test_fake_llm_reflects_last_user_message() -> None:
    llm = FakeLLM()
    messages = [
        LLMMessage(role="system", content="you are a buyer agent"),
        LLMMessage(role="user", content="hello world"),
    ]
    response = await llm.complete(messages)
    assert "hello world" in response.content


def test_get_llm_adapter_defaults_to_fake() -> None:
    adapter = get_llm_adapter("fake")
    assert isinstance(adapter, FakeLLM)


def test_get_llm_adapter_rejects_unimplemented_provider() -> None:
    with pytest.raises(NotImplementedError):
        get_llm_adapter("openai")
