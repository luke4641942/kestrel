"""Where every paper account stands, as one JSON snapshot, and the push that publishes it.

The ledgers live in ``data/`` on this Mac and are never pushed. The snapshot (and the
private to-do list beside it) is read away from it, from a separate private repository:
this repository is public. It is built with git's plumbing under a ref outside
``refs/heads`` (``refs/kestrel/status``), so no checkout, no index and no branch of this
repository is touched, and no ordinary push can carry it here. It is pushed to the other
repository's ``main``. A commit is made only when the content changes.

The snapshot holds only what the Trading page shows: no keys, no account identifiers.
Money is in USDT, the engine's currency.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from kestrel.config import Config
from kestrel.data.features import attach_features
from kestrel.data.store import ParquetStore
from kestrel.execution import paper, safety, tracking
from kestrel.strategies.conditions import Waiting
from kestrel.strategy import make_portfolio_strategy, make_strategy, spec_for
from kestrel.validation import decay

SCHEMA = 1
REF = "refs/kestrel/status"
"""Local only: outside refs/heads, so ``git push`` of branches never sends it."""
REMOTE = "status"
"""The git remote of the private repository it is published to."""
REMOTE_BRANCH = "main"
FILE = "paper-status.json"
REFERENCE = "written_at"
"""The one field ignored when deciding whether anything changed."""


def market(terms: paper.Terms, data_dir: Path | str, now: datetime | None = None) -> pl.DataFrame:
    """What the account's strategy reads: a basket's coin panel, or one market's bars with
    its features."""
    store = ParquetStore(data_dir)
    spec = spec_for(terms.strategy)
    if spec.universe:
        return paper.basket_panel(store, terms, list(spec.assets), now or datetime.now(UTC))
    bars = store.read(terms.symbol, terms.interval)
    return attach_features(bars, spec.features, terms.symbol, terms.interval, data_dir)


def tracked(account: paper.PaperAccount, frame: pl.DataFrame, config: Config) -> tracking.Tracking:
    """An account against its replay on the simulated venue (the model)."""
    if spec_for(account.terms().strategy).universe:
        return tracking.track_basket(account, frame, config)
    return tracking.track(account, frame, config)


def waiting(terms: paper.Terms, frame: pl.DataFrame) -> Waiting | None:
    """The strategy's entry tests at the latest stored close, if it describes them. None
    too when an input is missing: a view shows nothing rather than failing."""
    if spec_for(terms.strategy).universe:
        strategy: object = make_portfolio_strategy(terms.strategy, terms.params)
    else:
        strategy = make_strategy(terms.strategy, terms.symbol, terms.params)
    describe = getattr(strategy, "waiting", None)
    if describe is None:
        return None
    try:
        found: Waiting | None = describe(frame)
    except ValueError:
        return None
    return found


def hold_return(
    terms: paper.Terms, marks: list[dict[str, Any]], data_dir: Path | str, config: Config
) -> float | None:
    """What holding the account's market made over the same days: bought at the close of
    the first bar the account handled (when it was all cash) and held to its last, paying
    one entry's fee and half-spread (the backtest benchmark's costs). A basket holds its
    coins in equal parts. Closes are the market's, from the store (a testnet account's own
    fills are at testnet prices). None before any mark, or when a close is missing."""
    if not marks:
        return None
    first, last = (datetime.fromisoformat(str(marks[i]["bar"])) for i in (0, -1))
    spec = spec_for(terms.strategy)
    coins = spec.assets if spec.universe else (terms.symbol,)
    store = ParquetStore(data_dir)
    growth: list[float] = []
    for coin in coins:
        bars = store.read(coin, terms.interval)
        at = dict(zip(bars["open_time"].to_list(), bars["close"].to_list(), strict=True))
        if first not in at or last not in at:
            return None
        growth.append(float(at[last]) / float(at[first]))
    entry = (1 + float(config.costs.half_spread_bps) / 10_000) * (
        1 + float(config.costs.taker_fee_bps) / 10_000
    )
    return sum(growth) / len(growth) / entry - 1


def _money(x: Decimal | None) -> float | None:
    return None if x is None else round(float(x), 2)


def _day(t: datetime | None) -> str | None:
    return None if t is None else t.date().isoformat()


def account_status(
    account: paper.PaperAccount,
    data_dir: Path,
    config: Config,
    reference: Callable[[], list[float]] | None = None,
) -> dict[str, Any]:
    """One account. ``reference`` is the decay check's backtest returns (computed only
    once the check is due); by default the account's own."""
    terms, state = account.terms(), account.state()
    events = account.ledger.events()
    frame = market(terms, data_dir)
    model = tracked(account, frame, config)
    marks = [e for e in events if e["type"] == "mark"]
    equity = Decimal(str(marks[-1]["equity"])) if marks else None
    trips = tracking.round_trips(events, terms.symbol)
    basket = spec_for(terms.strategy).universe
    holding = (
        {k: float(v) for k, v in state.positions.items() if v > 0}
        if basket
        else ({terms.symbol: float(state.position)} if state.position > 0 else {})
    )
    check = decay.assess(
        decay.model_returns(model),
        reference or (lambda: decay.reference_returns(terms, data_dir, config)),
    )
    found = waiting(terms, frame)
    held = hold_return(terms, marks, data_dir, config)
    profit_pct = None if equity is None else float((equity / terms.initial_cash - 1) * 100)
    return {
        "name": terms.name,
        "strategy": terms.strategy,
        "note": terms.research,
        "market": list(spec_for(terms.strategy).assets) if basket else [terms.symbol],
        "interval": terms.interval.value,
        "venue": terms.venue,
        "started": _day(terms.trade_from),
        "last_bar": _day(state.last_bar),
        "days_handled": len(marks),
        "initial_usdt": _money(terms.initial_cash),
        "equity_usdt": _money(equity),
        "profit_usdt": None if equity is None else _money(equity - terms.initial_cash),
        "profit_pct": None if profit_pct is None else round(profit_pct, 2),
        "hold_pct": None if held is None else round(held * 100, 2),
        "vs_hold_pct": None
        if held is None or profit_pct is None
        else round(profit_pct - held * 100, 2),
        "cash_usdt": _money(state.cash),
        "exposure_pct": None
        if not equity
        else round(float((equity - state.cash) / equity * 100), 1),
        "holding": holding,
        "halted": safety.halted(account),
        "trades": len(trips),
        "open_trades": sum(t.closed is None for t in trips),
        "fills": len(model.fills),
        "model": {
            "equity_usdt": _money(model.days[-1].expected) if model.days else None,
            "decision_mismatches": model.decision_mismatches,
            "missed_days": model.missed,
        },
        "decay": {
            "status": check.status,
            "days": check.days,
            "due_at_days": decay.MIN_DAYS,
            "sharpe": None if check.sharpe is None else round(check.sharpe, 2),
            "floor": None if check.floor is None else round(check.floor, 2),
        },
        "waiting": None if found is None else _waiting_out(found),
    }


def _waiting_out(w: Waiting) -> dict[str, Any]:
    return {
        "bar": _day(w.bar),
        "rule": w.rule,
        "decides": w.decides,
        "would_hold": w.would_hold,
        "conditions": [
            {k: round(v, 6) if isinstance(v, float) else v for k, v in asdict(c).items()}
            for c in w.conditions
        ],
    }


def snapshot(data_dir: Path, config: Config, now: datetime | None = None) -> dict[str, Any]:
    """Every paper account, and all of them added up."""
    now = now or datetime.now(UTC)
    accounts = [
        account_status(paper.PaperAccount(data_dir, n), data_dir, config)
        for n in paper.accounts(data_dir)
    ]
    marked = [a for a in accounts if a["equity_usdt"] is not None]
    initial = sum(a["initial_usdt"] for a in accounts)
    equity = sum(a["equity_usdt"] for a in marked) + sum(
        a["initial_usdt"] for a in accounts if a["equity_usdt"] is None
    )
    bars = [a["last_bar"] for a in accounts if a["last_bar"]]
    return {
        "schema": SCHEMA,
        REFERENCE: now.replace(microsecond=0).isoformat(),
        "as_of": max(bars) if bars else None,
        "currency": "USDT",
        "note": "Binance Spot Testnet paper accounts (testnet prices aren't the market's). "
        "last_bar is the open date of the last daily bar handled; decisions are made at "
        "its close.",
        "total": {
            "accounts": len(accounts),
            "initial_usdt": round(initial, 2),
            "equity_usdt": round(equity, 2),
            "profit_usdt": round(equity - initial, 2),
            "halted": [a["name"] for a in accounts if a["halted"]],
            "behind": [
                a["name"] for a in accounts if a["last_bar"] and bars and a["last_bar"] < max(bars)
            ],
            "decision_mismatches": sum(a["model"]["decision_mismatches"] for a in accounts),
        },
        "accounts": accounts,
    }


def render(snap: dict[str, Any]) -> str:
    return json.dumps(snap, indent=2, sort_keys=False) + "\n"


# ---------- publishing ----------

Git = Callable[[list[str], str | None], subprocess.CompletedProcess[str]]


def git_in(repo: Path) -> Git:
    def run(args: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    return run


class PublishError(Exception):
    """A git step failed; nothing was half-written."""


def _ok(git: Git, args: list[str], stdin: str | None = None) -> str:
    done = git(args, stdin)
    if done.returncode != 0:
        raise PublishError(f"git {args[0]} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def _ref(git: Git, ref: str) -> str | None:
    done = git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], None)
    return done.stdout.strip() if done.returncode == 0 else None


def _comparable(name: str, text: str) -> object:
    """The content that counts as a change: the snapshot without its writing time."""
    if name != FILE:
        return text
    try:
        snap = json.loads(text)
    except json.JSONDecodeError:
        return text
    snap.pop(REFERENCE, None)
    return snap


def _unchanged(git: Git, parent: str, files: dict[str, str]) -> bool:
    if _ok(git, ["ls-tree", "--name-only", parent]).splitlines() != sorted(files):
        return False
    for name, text in files.items():
        stored = git(["show", f"{parent}:{name}"], None)
        if stored.returncode != 0:
            raise PublishError(f"git show failed: {stored.stderr.strip()}")
        if _comparable(name, stored.stdout) != _comparable(name, text):
            return False
    return True


def publish(git: Git, files: dict[str, str], remote: str = REMOTE, push: bool = True) -> str:
    """Commit ``files`` (name to text) at REF when anything changed, then push it to the
    remote's REMOTE_BRANCH if the remote is behind. Returns what happened, in a line."""
    if not files:
        raise PublishError("nothing to publish")
    parent = _ref(git, REF)
    committed = None
    if parent is None or not _unchanged(git, parent, files):
        lines = []
        for name in sorted(files):
            blob = _ok(git, ["hash-object", "-w", "--stdin"], files[name])
            lines.append(f"100644 blob {blob}\t{name}\n")
        tree = _ok(git, ["mktree"], "".join(lines))
        as_of = json.loads(files[FILE]).get("as_of") if FILE in files else None
        committed = _ok(
            git,
            ["commit-tree", tree, *(["-p", parent] if parent else []),
             "-m", f"Status as of {as_of}"],
        )  # fmt: skip
        # Moves the ref only if nobody else moved it since it was read.
        _ok(git, ["update-ref", REF, committed, parent or "0" * 40])
    head = committed or parent
    said = f"committed {committed[:7]}" if committed else "unchanged"
    if not push:
        return said
    tracking_ref = f"refs/remotes/{remote}/{REMOTE_BRANCH}"
    if _ref(git, tracking_ref) == head:
        return f"{said}; {remote} already has it"
    _ok(git, ["push", "--quiet", remote, f"{REF}:refs/heads/{REMOTE_BRANCH}"])
    # A push by refspec from a non-branch ref doesn't move the tracking ref: record it.
    _ok(git, ["update-ref", tracking_ref, str(head)])
    return f"{said}; pushed to {remote}"
