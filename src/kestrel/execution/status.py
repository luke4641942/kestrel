"""Where every paper account stands, as one JSON snapshot, and the push that publishes it.

The ledgers live in ``data/`` on this Mac and are never pushed. The snapshot is read away
from it (another Claude app reads it from GitHub). It is published to its own branch,
``paper-status``, through git's plumbing: no checkout, no index, nothing on ``main``. So
it can't touch work in progress or sweep up unpushed commits, and CI (main only) never
runs for it. A commit is made only when the content changes.

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
BRANCH = "paper-status"
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
        "profit_pct": None
        if equity is None
        else round(float((equity / terms.initial_cash - 1) * 100), 2),
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
    """A git step failed; nothing on the branch was half-written."""


def _ok(git: Git, args: list[str], stdin: str | None = None) -> str:
    done = git(args, stdin)
    if done.returncode != 0:
        raise PublishError(f"git {args[0]} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def _ref(git: Git, ref: str) -> str | None:
    done = git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], None)
    return done.stdout.strip() if done.returncode == 0 else None


def _same(old: str, new: str) -> bool:
    try:
        a, b = json.loads(old), json.loads(new)
    except json.JSONDecodeError:
        return False
    a.pop(REFERENCE, None)
    b.pop(REFERENCE, None)
    return bool(a == b)


def publish(git: Git, text: str, remote: str = "origin", push: bool = True) -> str:
    """Commit ``text`` as FILE on BRANCH when it changed, then push the branch if the remote
    is behind. Returns what happened, in a line."""
    local = f"refs/heads/{BRANCH}"
    parent = _ref(git, local)
    committed = None
    if parent is None or not _same(_ok(git, ["show", f"{parent}:{FILE}"]), text):
        blob = _ok(git, ["hash-object", "-w", "--stdin"], text)
        tree = _ok(git, ["mktree"], f"100644 blob {blob}\t{FILE}\n")
        message = f"Paper status as of {json.loads(text).get('as_of')}"
        committed = _ok(
            git, ["commit-tree", tree, *(["-p", parent] if parent else []), "-m", message]
        )
        # Moves the branch only if nobody else moved it since it was read.
        _ok(git, ["update-ref", local, committed, parent or "0" * 40])
    head = committed or parent
    said = f"committed {committed[:7]}" if committed else "unchanged"
    if not push:
        return said
    if _ref(git, f"refs/remotes/{remote}/{BRANCH}") == head:
        return f"{said}; {remote} already has it"
    _ok(git, ["push", "--quiet", remote, f"{local}:{local}"])
    return f"{said}; pushed {BRANCH} to {remote}"
