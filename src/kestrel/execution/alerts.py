"""Alerts: tell Luke when a paper account needs him, or did something.

After every scheduled run (`kestrel paper run`), the account's ledger is reviewed for:

- **halted**: a risk breach or failed reconciliation stopped it (only `kestrel paper resume`
  lifts it);
- **failed**: the run itself failed (stale or bad data, the exchange unreachable...), once a
  day per distinct error;
- **behind**: no bar handled for over a day past when it should have been (the Mac asleep,
  or the schedule broken);
- **missed**: a day recorded as missed (the run was late, so it didn't trade);
- **trade**: it bought or sold;
- **decay**: the decay check (kestrel.validation.decay) flagged its model replay, once per
  account: a prompt to look, never a stop.

Each alert has a key; sent keys are appended to ``alerts.jsonl`` in the account's folder, so
nothing is sent twice. The first review of an account only records what is already there:
switching alerts on never replays old history. Delivery is a macOS notification
(``mac_notifier``); the sender is injected, so tests run without one.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kestrel.execution import safety
from kestrel.execution.paper import PaperAccount

if TYPE_CHECKING:
    from kestrel.validation.decay import Decay

Kind = Literal["halted", "failed", "behind", "missed", "trade", "decay"]
LOG = "alerts.jsonl"


@dataclass(frozen=True)
class Alert:
    account: str
    kind: Kind
    key: str
    """Identifies the event, so it is sent once."""
    message: str


Send = Callable[[Alert], None]


def found(
    account: PaperAccount, now: datetime, failure: str | None = None, decay: Decay | None = None
) -> list[Alert]:
    """Everything worth an alert in the account now (sent or not)."""
    name = account.dir.name
    terms = account.terms()
    out: list[Alert] = []
    events = account.ledger.events()
    halt_reason = safety.halted(account)
    if halt_reason is not None:
        at = next(e["at"] for e in reversed(events) if e["type"] == "halt")
        out.append(Alert(name, "halted", f"halted:{at}", f"Halted: {halt_reason}"))
    if failure is not None:
        digest = hashlib.sha256(failure.encode()).hexdigest()[:12]
        first_line = failure.strip().splitlines()[0] if failure.strip() else "unknown error"
        out.append(
            Alert(name, "failed", f"failed:{now:%Y-%m-%d}:{digest}", f"Run failed: {first_line}")
        )
    last = account.state().last_bar
    if last is not None and halt_reason is None:
        late = safety.behind(last + terms.interval.duration, terms.interval, now)
        if late is not None:
            out.append(
                Alert(
                    name, "behind", f"behind:{last.isoformat()}",
                    f"No bar handled since {last:%Y-%m-%d} ({late}). Is the Mac asleep, or the "
                    "schedule broken?",
                )
            )  # fmt: skip
    for e in events:
        if e["type"] == "decision" and e.get("missed"):
            out.append(
                Alert(name, "missed", f"missed:{e['bar']}",
                      f"Missed {e['bar'][:10]}: the run was late, so it didn't trade")
            )  # fmt: skip
        if e["type"] == "fill":
            symbol = str(e.get("symbol") or terms.symbol)
            coin = symbol.removesuffix("USDT")
            value = float(e["quantity"]) * float(e["price"])
            out.append(
                Alert(
                    name, "trade", f"fill:{e['at']}:{symbol}:{e['side']}",
                    f"{str(e['side']).capitalize()} {e['quantity']} {coin} at "
                    f"{float(e['price']):,.2f} ({value:,.2f} USDT)",
                )
            )  # fmt: skip
        if e["type"] == "kill":
            out.append(Alert(name, "halted", f"kill:{e['at']}", "Killed: sold to cash and halted"))
    if decay is not None and decay.status == "flagged":
        out.append(
            Alert(
                name, "decay", "decay:flagged",
                f"Decay check flagged: Sharpe {decay.sharpe:.2f} over {decay.days} days, below "
                f"its backtest's 1-in-20 floor ({decay.floor:.2f}). Worth a look; nothing changes "
                "without Luke.",
            )
        )  # fmt: skip
    return out


def _log(account: PaperAccount) -> Path:
    return account.dir / LOG


def sent_keys(account: PaperAccount) -> set[str] | None:
    """Keys already sent, or None if the account has never been reviewed."""
    path = _log(account)
    if not path.exists():
        return None
    return {json.loads(line)["key"] for line in path.read_text().splitlines() if line.strip()}


def _record(account: PaperAccount, alerts: Sequence[Alert], at: datetime, sent: bool) -> None:
    path = _log(account)
    with path.open("a") as f:
        for a in alerts:
            f.write(json.dumps({**asdict(a), "at": at.isoformat(), "sent": sent}) + "\n")
    if not alerts and not path.exists():
        path.touch()


def review(
    account: PaperAccount,
    now: datetime,
    send: Send,
    failure: str | None = None,
    decay: Decay | None = None,
) -> list[Alert]:
    """Send every new alert (the first review only records), and return what was sent."""
    current = found(account, now, failure, decay)
    seen = sent_keys(account)
    if seen is None:
        # First review: history isn't news. A failure happening now still is.
        quiet = [a for a in current if a.kind != "failed"]
        _record(account, quiet, now, sent=False)
        seen = {a.key for a in quiet}
    new = [a for a in current if a.key not in seen]
    for a in new:
        send(a)
    _record(account, new, now, sent=True)
    return new


def mac_notifier(run: Callable[..., object] = subprocess.run) -> Send:
    """A macOS notification per alert. The text goes to osascript as arguments, never into
    the script, so a message can't break or change it."""

    def send(alert: Alert) -> None:
        sound = "Basso" if alert.kind in ("halted", "failed", "behind") else "Glass"
        run(
            [
                "osascript",
                "-e", "on run argv",
                "-e", "display notification (item 2 of argv) with title (item 1 of argv) "
                      f'sound name "{sound}"',
                "-e", "end run",
                f"Kestrel: {alert.account}",
                alert.message,
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )  # fmt: skip

    return send
