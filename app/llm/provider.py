"""LLM provider layer: one real backend (Ollama), a disabled backend and a
fake for tests, behind a common Protocol. No caller ever needs to know which
one is active -- an unavailable/broken model just raises LLMUnavailable and
routes fall back to manual mode.
"""

from __future__ import annotations

import logging
import time as time_module
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.config import LLMConfig

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(Exception):
    """Provider disabled, unreachable, timed out, or returned unusable output."""


@dataclass
class ProviderHealth:
    provider: str
    model: str
    reachable: bool
    model_present: bool
    loaded: bool
    detail: str = ""


class LLMProvider(Protocol):
    name: str
    model: str

    def generate_json(self, system: str, user: str, schema: type[T]) -> T: ...

    def health(self) -> ProviderHealth: ...

    def preload(self) -> None: ...


class _BaseProvider:
    """Shared JSON validation/retry logic. Subclasses implement `_raw`."""

    name: str = "base"
    model: str = ""
    invalid_outputs: int = 0

    def _raw(self, system: str, user: str, schema: type[BaseModel]) -> str:
        raise NotImplementedError

    def generate_json(self, system: str, user: str, schema: type[T]) -> T:
        for _attempt in range(2):
            raw = self._raw(system, user, schema)
            try:
                return schema.model_validate_json(raw)
            except ValidationError:
                self.invalid_outputs += 1
                continue
        raise LLMUnavailable("invalid output")


class OllamaProvider(_BaseProvider):
    def __init__(self, cfg: LLMConfig):
        if cfg.model.split(":")[-1].endswith("cloud") and not cfg.allow_cloud:
            raise ValueError(f"cloud model refused: {cfg.model}")
        import ollama

        self.name = "ollama"
        self.model = cfg.model
        self.cfg = cfg
        self.keep_alive = cfg.keep_alive
        self.num_ctx = cfg.num_ctx
        self._client = ollama.Client(host=cfg.host, timeout=cfg.timeout_s)
        self._health_client = ollama.Client(host=cfg.host, timeout=2.0)
        self._use_think = True
        self._health_cache: ProviderHealth | None = None
        self._health_cache_at: float = 0.0

    def _raw(self, system: str, user: str, schema: type[BaseModel]) -> str:
        import httpx
        import ollama

        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format=schema.model_json_schema(),
            options={"temperature": 0, "seed": 7, "num_ctx": self.num_ctx},
            keep_alive=self.keep_alive,
        )
        try:
            if self._use_think:
                try:
                    resp = self._client.chat(**kwargs, think=False)
                except TypeError:
                    # Some Ollama client/model combos reject `think`; retry
                    # once without it and stop offering it from then on.
                    self._use_think = False
                    resp = self._client.chat(**kwargs)
            else:
                resp = self._client.chat(**kwargs)
            return resp.message.content
        except ollama.ResponseError as exc:
            raise LLMUnavailable(str(exc)) from exc
        except (httpx.TimeoutException, httpx.ConnectError, ConnectionError) as exc:
            raise LLMUnavailable(str(exc)) from exc

    def health(self) -> ProviderHealth:
        now = time_module.monotonic()
        if self._health_cache is not None and (now - self._health_cache_at) < 10:
            return self._health_cache

        try:
            models = self._health_client.list()
            model_names = [m.get("model") or m.get("name") for m in _as_list(models)]
            reachable = True
            model_present = self.model in model_names
        except Exception as exc:  # noqa: BLE001
            health = ProviderHealth(
                provider=self.name, model=self.model, reachable=False,
                model_present=False, loaded=False, detail=str(exc),
            )
            self._health_cache, self._health_cache_at = health, now
            return health

        loaded = False
        try:
            running = self._health_client.ps()
            running_names = [m.get("model") or m.get("name") for m in _as_list(running)]
            loaded = self.model in running_names
        except Exception:  # noqa: BLE001
            loaded = False

        health = ProviderHealth(
            provider=self.name, model=self.model, reachable=reachable,
            model_present=model_present, loaded=loaded, detail="",
        )
        self._health_cache, self._health_cache_at = health, now
        return health

    def preload(self) -> None:
        try:
            self._client.chat(model=self.model, messages=[], keep_alive=self.keep_alive)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM preload failed: %s", exc)


def _as_list(resp) -> list[dict]:
    """ollama's list()/ps() return an object with a `.models`/`.processes`
    attribute across client versions; normalize to a list of dicts."""
    if isinstance(resp, dict):
        items = resp.get("models") or resp.get("processes") or []
    else:
        items = getattr(resp, "models", None) or getattr(resp, "processes", None) or []
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
        else:
            out.append({"model": getattr(item, "model", None) or getattr(item, "name", None)})
    return out


class NullProvider(_BaseProvider):
    def __init__(self, detail: str):
        self.name = "none"
        self.model = ""
        self.detail = detail

    def generate_json(self, system: str, user: str, schema: type[T]) -> T:
        raise LLMUnavailable(self.detail)

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name, model=self.model, reachable=False,
            model_present=False, loaded=False, detail=self.detail,
        )

    def preload(self) -> None:
        pass


class FakeProvider(_BaseProvider):
    """Test double. `responses` items are popped in order: a string is
    returned as raw JSON text, an Exception instance/class is raised."""

    def __init__(self, responses: list):
        self.name = "fake"
        self.model = "fake-model"
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def _raw(self, system: str, user: str, schema: type[BaseModel]) -> str:
        self.calls.append((system, user))
        if not self._responses:
            raise LLMUnavailable("no more fake responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, type) and issubclass(item, Exception):
            raise item()
        return item

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name, model=self.model, reachable=True,
            model_present=True, loaded=True, detail="",
        )

    def preload(self) -> None:
        pass


def get_provider(cfg: LLMConfig | None = None) -> LLMProvider:
    cfg = cfg or LLMConfig.from_env()
    if cfg.provider == "none":
        return NullProvider("LLM disabled")
    try:
        return OllamaProvider(cfg)
    except ValueError:
        return NullProvider("cloud model refused")


@lru_cache(maxsize=1)
def _cached_default_provider() -> LLMProvider:
    return get_provider(LLMConfig.from_env())


def get_provider_dependency() -> LLMProvider:
    """FastAPI dependency: the process-wide provider built from env config.
    Tests override this via app.dependency_overrides instead of calling it."""
    return _cached_default_provider()
