from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.assistant import (
    MODEL,
    AssistantError,
    ResearchAssistant,
    credentials_hint,
    strategy_context,
)
from kestrel.config import load_config
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.research import SECTIONS, ResearchStore
from kestrel.strategy import strategy_source
from tests.backtest.helpers import bars

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"
FILLED = {k: f"{k} for the trend idea" for k in SECTIONS}


class FakeMessages:
    """Records every request; answers with canned content."""

    def __init__(self, content: list[Any] | None = None, stop_reason: str = "end_turn") -> None:
        self.calls: list[dict[str, Any]] = []
        self.content = content or [SimpleNamespace(type="text", text="Who is on the other side?")]
        self.stop_reason = stop_reason
        self.error: Exception | None = None

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop_reason, content=self.content)


def fake_assistant(messages: FakeMessages) -> ResearchAssistant:
    return ResearchAssistant(client=SimpleNamespace(messages=messages))


@pytest.fixture
def note(tmp_path: Path) -> Any:
    store = ResearchStore(tmp_path)
    slug = store.create("BTC trend").slug
    store.edit(slug, 1, FILLED, "ma-trend")
    return store.get(slug)


def test_request_shape(note: Any) -> None:
    messages = FakeMessages()
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "system", "content": "ignored"},
    ]
    answer = fake_assistant(messages).reply(
        note, "class X: ...", history, "Is this worth testing?", load_config(REPO_CONFIG)
    )
    assert answer == "Who is on the other side?"
    (call,) = messages.calls
    assert call["model"] == MODEL == "claude-opus-5-5"
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"] == {"effort": "medium"}
    assert "thinking" not in call  # Opus 5.5 thinks adaptively; disabling it is a 400
    stable, context = call["system"]
    assert stable["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in context  # the volatile part sits after the breakpoint
    assert [m["role"] for m in call["messages"]] == ["user", "assistant", "user"]
    assert call["messages"][-1]["content"] == "Is this worth testing?"


def test_system_prompt_carries_the_platform_rules(note: Any) -> None:
    messages = FakeMessages()
    fake_assistant(messages).reply(note, None, [], "hi", load_config(REPO_CONFIG))
    stable = messages.calls[0]["system"][0]["text"]
    assert "taker fee 7.5 bps" in stable
    assert "round trip costs about 17 bps" in stable
    assert "2025-11-01" in stable
    assert "never see backtest results" in stable
    assert "long-only" in stable


def test_context_is_the_note_and_the_code(note: Any) -> None:
    text = strategy_context(note, strategy_source("ma-trend"))
    assert "Research note: version 1, draft (editable)" in text
    for heading in SECTIONS.values():
        assert f"## {heading}" in text
    assert "hypothesis for the trend idea" in text
    assert "class MovingAverageTrend" in text


def test_context_explains_why_a_version_exists(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    slug = store.create("Trend").slug
    store.edit(slug, 1, FILLED, "ma-trend")
    store.freeze(slug, 1)
    store.new_version(slug, "Turnover was higher than the note assumed")
    text = strategy_context(store.get(slug), None)
    assert "version 2, draft" in text
    assert "Why this version exists: Turnover was higher than the note assumed" in text


def test_context_marks_missing_parts(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    s = store.create("Empty")
    text = strategy_context(s, None)
    assert "(not written yet)" in text
    assert "(no code yet)" in text


def test_only_text_blocks_are_returned(note: Any) -> None:
    messages = FakeMessages(
        [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="Part one."),
            SimpleNamespace(type="fallback"),
            SimpleNamespace(type="text", text="Part two."),
        ]
    )
    answer = fake_assistant(messages).reply(note, None, [], "hi", load_config(REPO_CONFIG))
    assert answer == "Part one.\n\nPart two."


@pytest.mark.parametrize(
    ("content", "stop", "message"),
    [
        (None, "refusal", "declined"),
        ([SimpleNamespace(type="thinking", thinking="")], "end_turn", "empty answer"),
    ],
)
def test_unusable_answers_raise(note: Any, content: Any, stop: str, message: str) -> None:
    messages = FakeMessages(content, stop)
    with pytest.raises(AssistantError, match=message):
        fake_assistant(messages).reply(note, None, [], "hi", load_config(REPO_CONFIG))


def _status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(code, request=request), body=None)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (_status_error(anthropic.AuthenticationError, 401), "No Anthropic credentials"),
        (_status_error(anthropic.RateLimitError, 429), "Rate limited"),
        (_status_error(anthropic.InternalServerError, 500), "API error 500"),
        (
            anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")),
            "Couldn't reach",
        ),
        (TypeError("Could not resolve authentication method."), "No Anthropic credentials"),
    ],
)
def test_api_failures_become_clear_messages(note: Any, error: Exception, message: str) -> None:
    messages = FakeMessages()
    messages.error = error
    with pytest.raises(AssistantError, match=message):
        fake_assistant(messages).reply(note, None, [], "hi", load_config(REPO_CONFIG))


def test_unrelated_type_errors_are_not_hidden(note: Any) -> None:
    messages = FakeMessages()
    messages.error = TypeError("a real bug")
    with pytest.raises(TypeError, match="a real bug"):
        fake_assistant(messages).reply(note, None, [], "hi", load_config(REPO_CONFIG))


def test_default_client_is_the_anthropic_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = ResearchAssistant()._beta()
    assert hasattr(client, "messages")


def test_credentials_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert "ANTHROPIC_API_KEY" in (credentials_hint() or "")
    (tmp_path / ".config" / "anthropic").mkdir(parents=True)
    assert credentials_hint() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert credentials_hint() is None


# ---------- through the API ----------


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, FakeMessages]:
    ParquetStore(tmp_path / "data").write(
        "BTCUSDT", Interval.H4, bars([100.0 + (i % 9) * 2 for i in range(120)])
    )
    messages = FakeMessages()
    app = create_app(
        tmp_path / "data",
        static_dir=None,
        config_path=REPO_CONFIG,
        research_dir=tmp_path / "research",
        assistant=fake_assistant(messages),
    )
    return TestClient(app), messages


def test_chat_is_saved_with_the_strategy(api: tuple[TestClient, FakeMessages]) -> None:
    client, messages = api
    slug = client.post("/api/research", json={"name": "BTC trend"}).json()["slug"]
    detail = client.post(f"/api/research/{slug}/chat", json={"message": "Thoughts?"}).json()
    assert [(m["role"], m["content"]) for m in detail["chat"]] == [
        ("user", "Thoughts?"),
        ("assistant", "Who is on the other side?"),
    ]
    assert detail["chat"][0]["note_version"] == 1
    # The second turn sends the saved conversation back.
    client.post(f"/api/research/{slug}/chat", json={"message": "And costs?"})
    assert [m["content"] for m in messages.calls[1]["messages"]] == [
        "Thoughts?",
        "Who is on the other side?",
        "And costs?",
    ]


def test_the_assistant_never_receives_results(api: tuple[TestClient, FakeMessages]) -> None:
    client, messages = api
    slug = client.post("/api/research", json={"name": "BTC trend"}).json()["slug"]
    client.put(
        f"/api/research/{slug}/notes/1", json={"sections": FILLED, "implementation": "ma-trend"}
    )
    client.post(f"/api/research/{slug}/notes/1/freeze")
    job = client.post(
        "/api/jobs/backtest",
        json={
            "symbol": "BTCUSDT",
            "interval": "4h",
            "start": "2024-01",
            "end": "2024-01",
            "research": slug,
        },
    ).json()
    deadline = time.monotonic() + 10
    while client.get(f"/api/jobs/{job['id']}").json()["status"] not in {"succeeded", "failed"}:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    (run,) = client.get("/api/backtests").json()
    assert client.get(f"/api/research/{slug}").json()["trials"] == 1

    client.post(f"/api/research/{slug}/chat", json={"message": "How should I improve it?"})
    sent = json.dumps(messages.calls[0])
    # Only things that can come from results: the run, metric names and its numbers.
    # (Words like "equity" and "trial" appear legitimately in the platform rules.)
    assert run["id"] not in sent
    for metric in ("sharpe", "total_return", "max_drawdown", "drawdown", "psr", "cagr"):
        assert metric not in sent.lower()
    m = run["metrics"]
    for value in (m["final_equity"], m["total_return"], m["volatility"]):
        assert f"{value:.4f}" not in sent


def test_chat_errors(api: tuple[TestClient, FakeMessages], tmp_path: Path) -> None:
    client, messages = api
    slug = client.post("/api/research", json={"name": "BTC trend"}).json()["slug"]
    messages.error = _status_error(anthropic.AuthenticationError, 401)
    failed = client.post(f"/api/research/{slug}/chat", json={"message": "hi"})
    assert failed.status_code == 503
    assert "No Anthropic credentials" in failed.text
    assert client.get(f"/api/research/{slug}").json()["chat"] == []  # nothing half-saved
    assert client.post("/api/research/nope/chat", json={"message": "hi"}).status_code == 404

    disabled = TestClient(
        create_app(tmp_path / "d2", static_dir=None, research_dir=tmp_path / "research")
    )
    response = disabled.post(f"/api/research/{slug}/chat", json={"message": "hi"})
    assert response.status_code == 503
    assert "not enabled" in response.text
