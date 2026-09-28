import pytest
from pydantic import BaseModel

from app.llm.config import LLMConfig
from app.llm.provider import (
    FakeProvider,
    LLMUnavailable,
    NullProvider,
    OllamaProvider,
    get_provider,
)


class Dummy(BaseModel):
    value: int


@pytest.mark.parametrize("tag", ["glm-5.2:cloud", "gpt-oss:120b-cloud"])
def test_cloud_tags_refused_unless_allowed(tag):
    cfg = LLMConfig(model=tag, allow_cloud=False)
    with pytest.raises(ValueError):
        OllamaProvider(cfg)


@pytest.mark.parametrize("tag", ["glm-5.2:cloud", "gpt-oss:120b-cloud"])
def test_cloud_tags_allowed_when_flag_set(tag, monkeypatch):
    import ollama

    monkeypatch.setattr(ollama, "Client", _FakeOllamaClient)
    cfg = LLMConfig(model=tag, allow_cloud=True)
    provider = OllamaProvider(cfg)  # must not raise
    assert provider.model == tag


def test_get_provider_refuses_cloud_via_none_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "glm-5.2:cloud")
    monkeypatch.delenv("LLM_ALLOW_CLOUD", raising=False)
    provider = get_provider()
    assert isinstance(provider, NullProvider)
    assert provider.health().detail == "cloud model refused"


def test_fake_provider_retries_then_succeeds():
    provider = FakeProvider(["not json", '{"value": 5}'])
    result = provider.generate_json("sys", "user", Dummy)
    assert result.value == 5
    assert provider.invalid_outputs == 1


def test_fake_provider_bad_json_twice_raises():
    provider = FakeProvider(["nope", "still not json"])
    with pytest.raises(LLMUnavailable):
        provider.generate_json("sys", "user", Dummy)


def test_null_provider_raises_and_reports_unreachable():
    provider = NullProvider("LLM disabled")
    with pytest.raises(LLMUnavailable):
        provider.generate_json("sys", "user", Dummy)
    health = provider.health()
    assert health.reachable is False
    assert health.detail == "LLM disabled"


def test_get_provider_none():
    cfg = LLMConfig(provider="none")
    provider = get_provider(cfg)
    assert isinstance(provider, NullProvider)


class _FakeOllamaClient:
    def __init__(self, host=None, timeout=None):
        self.host = host
        self.timeout = timeout

    def chat(self, **kwargs):
        class Msg:
            content = '{"value": 42}'

        class Resp:
            message = Msg()

        return Resp()

    def list(self):
        return {"models": [{"model": "qwen3.8:27b"}]}

    def ps(self):
        return {"models": [{"model": "qwen3.8:27b"}]}


def test_ollama_provider_generate_json_via_monkeypatched_client(monkeypatch):
    import ollama

    monkeypatch.setattr(ollama, "Client", _FakeOllamaClient)
    cfg = LLMConfig(model="qwen3.8:27b")
    provider = OllamaProvider(cfg)
    result = provider.generate_json("sys", "user", Dummy)
    assert result.value == 42


def test_ollama_provider_health_via_monkeypatched_client(monkeypatch):
    import ollama

    monkeypatch.setattr(ollama, "Client", _FakeOllamaClient)
    cfg = LLMConfig(model="qwen3.8:27b")
    provider = OllamaProvider(cfg)
    health = provider.health()
    assert health.reachable is True
    assert health.model_present is True
    assert health.loaded is True


def test_llm_health_and_badge_route_with_fake_provider(client):
    from app.llm.provider import get_provider_dependency
    from app.main import app

    fake = FakeProvider([])
    app.dependency_overrides[get_provider_dependency] = lambda: fake
    try:
        r = client.get("/api/llm/health")
        assert r.status_code == 200
        assert r.json()["loaded"] is True

        r = client.get("/llm/badge")
        assert "fake-model" in r.text
    finally:
        app.dependency_overrides.pop(get_provider_dependency, None)


def test_llm_badge_offline_with_null_provider(client):
    from app.llm.provider import get_provider_dependency
    from app.main import app

    app.dependency_overrides[get_provider_dependency] = lambda: NullProvider("LLM disabled")
    try:
        r = client.get("/llm/badge")
        assert "offline" in r.text.lower()
    finally:
        app.dependency_overrides.pop(get_provider_dependency, None)
