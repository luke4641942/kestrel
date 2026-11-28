"""Alerts: tell Luke when a paper account needs him, or did something.

After every scheduled run (`kestrel paper run`), the account's ledger is reviewed for:

- **halted**: a risk breach or failed reconciliation stopped it (only `kestrel paper resume`
  lifts it);
- **failed**: the run itself failed (stale or bad data, a bug...), once a day per distinct
  error. A *network* failure (no connection, a name that won't resolve: the Mac just woke)
  alerts only the second time in a row for the account (Luke, 2026-11-23): the next hourly
  run retries anyway, and one blip woke him with nine notifications. The first is recorded,
  not sent; any run that gets through, or fails for another reason, resets the count
  (``network.json`` in the account's folder);
- **behind**: no bar handled for over a day past when it should have been (the Mac asleep,
  or the schedule broken);
- **missed**: a day recorded as missed (the run was late, so it didn't trade);
- **trade**: it bought or sold;
- **decay**: the decay check (kestrel.validation.decay) flagged its model replay, once per
  account: a prompt to look, never a stop.

Each alert has a key; sent keys are appended to ``alerts.jsonl`` in the account's folder, so
nothing is sent twice. The first review of an account only records what is already there:
switching alerts on never replays old history. Delivery is a macOS notification
(``mac_notifier``) and, when ``KESTREL_NTFY_TOPIC`` is set, a push to the phone through ntfy
(``ntfy_notifier``; Luke, 2026-11-28: a halt was missed away from the Mac). The topic is the
only secret (anyone who knows it can read the alerts), so it comes from the environment,
never a file, and is never printed. A failed push warns without the topic and never stops
the run or the Mac notification. Senders are injected, so tests run without either.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping, Sequence
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
NETWORK = "network.json"
NETWORK_IN_A_ROW = 2
"""Network failures in a row before one is sent."""


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


def _network_in_a_row(account: PaperAccount, network_failure: bool) -> int:
    """Count this run in the account's run of network failures (0 resets it)."""
    path = account.dir / NETWORK
    try:
        before = int(json.loads(path.read_text())["in_a_row"])
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        before = 0
    count = before + 1 if network_failure else 0
    account.dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"in_a_row": count}))
    return count


def review(
    account: PaperAccount,
    now: datetime,
    send: Send,
    failure: str | None = None,
    decay: Decay | None = None,
    network: bool = False,
) -> list[Alert]:
    """Send every new alert (the first review only records), and return what was sent.
    ``network``: the failure was the connection, not Kestrel or the data."""
    in_a_row = _network_in_a_row(account, failure is not None and network)
    current = found(account, now, failure, decay)
    seen = sent_keys(account)  # before anything is recorded: is this the first review?
    if failure is not None and network:
        failed = [a for a in current if a.kind == "failed"]
        current = [a for a in current if a.kind != "failed"]
        if in_a_row < NETWORK_IN_A_ROW:
            held = [
                Alert(
                    a.account,
                    "failed",
                    f"held:{now.isoformat()}",
                    f"{a.message} (held: first in a row)",
                )
                for a in failed
            ]
            _record(account, held, now, sent=False)
        else:
            current += [
                Alert(a.account, a.kind, a.key, f"{a.message} ({in_a_row} runs in a row)")
                for a in failed
            ]
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


NTFY_TOPIC_ENV = "KESTREL_NTFY_TOPIC"
NTFY_SERVER_ENV = "KESTREL_NTFY_SERVER"
NTFY_SERVER = "https://ntfy.sh"
_TOPIC = re.compile(r"[A-Za-z0-9_-]{1,64}")
URGENT: tuple[Kind, ...] = ("halted", "failed", "behind")
_TAGS: dict[Kind, str] = {
    "halted": "rotating_light",
    "failed": "warning",
    "behind": "zzz",
    "missed": "calendar",
    "trade": "chart_with_upwards_trend",
    "decay": "chart_with_downwards_trend",
}

Post = Callable[[str, bytes, dict[str, str]], None]
"""Sends one HTTP POST (url, body, headers); raises OSError on failure."""


def _urllib_post(url: str, body: bytes, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


def ntfy_notifier(
    topic: str,
    server: str = NTFY_SERVER,
    post: Post = _urllib_post,
    warn: Callable[[str], None] = _stderr,
) -> Send:
    """A phone push per alert through ntfy: urgent kinds at high priority."""
    if not _TOPIC.fullmatch(topic):
        raise ValueError(f"{NTFY_TOPIC_ENV} must be 1-64 letters, digits, '-' or '_'")
    if not server.startswith("https://"):
        raise ValueError(f"{NTFY_SERVER_ENV} must be an https:// address")
    url = f"{server.rstrip('/')}/{topic}"

    def send(alert: Alert) -> None:
        headers = {
            "Title": f"Kestrel: {alert.account}",
            "Priority": "high" if alert.kind in URGENT else "default",
            "Tags": _TAGS[alert.kind],
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "kestrel-research/0.1",
        }
        try:
            post(url, alert.message.encode(), headers)
        except (OSError, ValueError) as e:  # the error's text could hold the URL: name only
            warn(f"phone alert not sent ({type(e).__name__}); the Mac notification still was")

    return send


def every(*senders: Send) -> Send:
    """Each alert through every sender, in order."""

    def send(alert: Alert) -> None:
        for s in senders:
            s(alert)

    return send


def from_env(
    env: Mapping[str, str],
    mac: Send | None = None,
    post: Post = _urllib_post,
    warn: Callable[[str], None] = _stderr,
) -> Send:
    """The Mac notification, plus the phone when ``KESTREL_NTFY_TOPIC`` is set. A bad
    topic or server warns (without the value) and leaves the Mac notification alone."""
    senders = [mac if mac is not None else mac_notifier()]
    topic = env.get(NTFY_TOPIC_ENV, "").strip()
    if topic:
        try:
            senders.append(ntfy_notifier(topic, env.get(NTFY_SERVER_ENV, NTFY_SERVER), post, warn))
        except ValueError as e:
            warn(f"phone alerts off: {e}")
    return every(*senders)
