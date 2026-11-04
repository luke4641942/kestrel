"""The research assistant: an AI that helps think a strategy through, blind to results.

It is deliberately not part of a tuning loop. ``ResearchAssistant.reply`` accepts the
research note, the implementation's source and the conversation, and nothing else:
there is no parameter through which backtest results, metrics or the trial log could
reach it. It can challenge a hypothesis, check an idea against costs, spot lookahead,
draft plain-English rules and explain code; it cannot say "your Sharpe was 0.8, try a
50-day window", because it never sees the 0.8.

The user can still paste numbers into the chat. The system prompt tells the assistant
to decline to optimise on them; the structural guarantee is only that Kestrel never
sends them itself.

The Anthropic client is injected, so tests run offline with a fake.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from kestrel.config import Config
from kestrel.research import SECTIONS, ResearchStrategy

MODEL = "claude-opus-5-5"

SYSTEM = """\
You are the research assistant inside Kestrel, a systematic crypto trading platform \
built by Luke, a solo researcher with about A$500 of capital who wants to show quant \
employers rigorous, honest research.

You help with one strategy idea at a time. You are given its research note and, if it \
exists, the code that implements it.

You never see backtest results, by design. Kestrel keeps you out of the loop of \
"run a backtest, ask how to improve it, tweak, rerun", because that loop finds \
variants that fit past noise. If Luke pastes results or asks you to adjust parameters \
to improve a result, do not optimise against them. Explain that every change is a new \
version of the note and a new logged trial, and help him judge whether a change is \
justified by the hypothesis itself, independent of how the last run did.

Useful things you can do:
- Challenge the hypothesis: who is on the other side of the trade, and why hasn't the \
edge been arbitraged away?
- Check the idea against costs: estimate turnover from the rules and compare it with \
the round-trip cost below.
- Spot lookahead: rules that use information not available at the bar's close.
- Propose what result would prove the idea wrong, decided before any test.
- Draft or tighten the plain-English rules, and explain what the code does and \
whether it matches those rules.

Platform facts to respect:
- Binance spot only, long-only. A strategy outputs a target weight from 0 to 1 per \
bar (the rest is cash). No shorting until perpetuals in a later phase.
- A decision is made at a bar's close and filled at the next bar's open.
- Costs per side: taker fee {fee} bps, half-spread {spread} bps, plus square-root \
market impact. A round trip costs about {round_trip} bps.
- Rebalances smaller than {band:.0%} of equity are skipped (no-trade band).
- Bars are 4h or daily; low turnover is essential at this account size.
- Data from {holdout} on is a reserved holdout, used once for a final test. Never \
suggest using it.

Be direct and concise. Push back on ideas that would overfit or fool him. Write in \
plain English, and say when you are unsure.\
"""


class MessagesClient(Protocol):
    """The slice of ``anthropic.Anthropic().beta`` the assistant uses."""

    @property
    def messages(self) -> Any: ...


class AssistantError(RuntimeError):
    """The assistant couldn't answer (no credentials, API error, refusal)."""


class ResearchAssistant:
    def __init__(self, client: MessagesClient | None = None, model: str = MODEL) -> None:
        self._client = client
        self.model = model

    def _beta(self) -> MessagesClient:
        if self._client is None:
            import anthropic

            # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
            self._client = anthropic.Anthropic().beta
        return self._client

    def reply(
        self,
        strategy: ResearchStrategy,
        source: str | None,
        history: list[dict[str, Any]],
        message: str,
        config: Config,
    ) -> str:
        """Answer ``message`` in the context of the note and code. Never sees results."""
        import anthropic

        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in history
            if m.get("role") in {"user", "assistant"}
        ]
        messages.append({"role": "user", "content": message})
        try:
            response = self._beta().messages.create(
                model=self.model,
                max_tokens=16000,
                output_config={"effort": "medium"},
                # Retry a refused request on a fallback model chosen by the API.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[
                    {
                        "type": "text",
                        "text": system_prompt(config),
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": strategy_context(strategy, source)},
                ],
                messages=messages,
            )
        except anthropic.AuthenticationError:
            raise AssistantError(
                "No Anthropic credentials. Set ANTHROPIC_API_KEY before `kestrel serve`."
            ) from None
        except anthropic.RateLimitError:
            raise AssistantError("Rate limited by the Anthropic API; try again shortly.") from None
        except anthropic.APIStatusError as e:
            raise AssistantError(f"Anthropic API error {e.status_code}: {e.message}") from None
        except anthropic.APIConnectionError:
            raise AssistantError("Couldn't reach the Anthropic API.") from None
        except TypeError as e:  # SDK 2.x raises TypeError when no credential source exists
            if "authentication" in str(e).lower() or "api_key" in str(e).lower():
                raise AssistantError(
                    "No Anthropic credentials. Set ANTHROPIC_API_KEY before `kestrel serve`."
                ) from None
            raise

        if response.stop_reason == "refusal":
            raise AssistantError("The assistant declined to answer that.")
        text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise AssistantError("The assistant returned an empty answer.")
        return text


def credentials_hint() -> str | None:
    """None when an Anthropic credential source looks configured, else how to set one."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return None
    if (Path.home() / ".config" / "anthropic").is_dir():  # `ant auth login` profile
        return None
    return "Set ANTHROPIC_API_KEY (or run `ant auth login`) and restart `kestrel serve`."


def system_prompt(config: Config) -> str:
    c = config.costs
    side = float(c.taker_fee_bps + c.half_spread_bps)
    return SYSTEM.format(
        fee=float(c.taker_fee_bps),
        spread=float(c.half_spread_bps),
        round_trip=round(2 * side),
        band=float(config.backtest.no_trade_band),
        holdout=f"{config.backtest.holdout_start:%Y-%m-%d}",
    )


def strategy_context(strategy: ResearchStrategy, source: str | None) -> str:
    """The note and code, as the assistant sees them. Contains no results by design."""
    note = strategy.latest
    state = f"frozen {note.frozen_at[:10]}" if note.frozen_at else "draft (editable)"
    lines = [
        f"Strategy: {strategy.name}",
        f"Status: {strategy.status}",
        f"Research note: version {note.version}, {state}",
    ]
    if note.change_reason:
        lines.append(f"Why this version exists: {note.change_reason}")
    for key, heading in SECTIONS.items():
        lines += ["", f"## {heading}", note.sections.get(key, "").strip() or "(not written yet)"]
    lines += ["", f"## Implementation: {note.implementation or 'none chosen yet'}"]
    lines.append(f"```python\n{source}\n```" if source else "(no code yet)")
    return "\n".join(lines)


def chat_record(role: str, content: str, note_version: int) -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "at": datetime.now(UTC).isoformat(),
        "note_version": note_version,
    }
