"""Tracking: what a paper account did, against what the backtest model says it would have.

The expected account is the same strategy, terms and bars replayed on the simulated venue
(``kestrel.execution.paper.run``), which is the backtest by construction. Comparing them:

- **Decisions** must be identical: the same code decides in both. Any mismatch is a bug.
- **Fills** show the gap between the cost model and a real venue: the price paid against
  the reference (the bar's open) and the fee, in basis points of the order, for each side.
- **Equity** shows the total effect, including days a live account missed.

For an account on the simulated venue the two are identical; that is the check that the
comparison itself is sound. On the testnet, prices come from Binance's test order book, not
the real market, so its costs aren't real costs; only live fills measure those.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

import polars as pl

from kestrel.config import Config
from kestrel.execution.paper import PaperAccount, run, run_basket

Decided = Decimal | dict[str, str | None] | None
"""A single coin's target weight, or a basket's targets (symbol -> weight; None when the
strategy made no decision that bar)."""


@dataclass(frozen=True)
class DayTrack:
    bar: datetime
    equity: Decimal
    expected: Decimal
    target: Decided
    expected_target: Decided
    missed: bool


@dataclass(frozen=True)
class FillTrack:
    at: str
    symbol: str
    side: str
    cost_bps: Decimal
    """Price against the reference plus the fee, in bps of the order: the real cost."""
    expected_bps: Decimal | None
    """The cost model's figure for the same order, if the replay traded then too."""


@dataclass(frozen=True)
class Tracking:
    days: list[DayTrack]
    fills: list[FillTrack]

    @property
    def decision_mismatches(self) -> int:
        return sum(d.target != d.expected_target for d in self.days)

    @property
    def missed(self) -> int:
        return sum(d.missed for d in self.days)

    @property
    def difference(self) -> Decimal | None:
        """Final equity against expected, as a fraction (-0.01: 1% behind the model)."""
        if not self.days or self.days[-1].expected == 0:
            return None
        return self.days[-1].equity / self.days[-1].expected - 1

    def mean_cost(self, expected: bool = False) -> Decimal | None:
        values = [f.expected_bps if expected else f.cost_bps for f in self.fills]
        known = [v for v in values if v is not None]
        return sum(known, Decimal(0)) / len(known) if known else None


def _cost_bps(fill: dict[str, Any]) -> Decimal:
    reference, price = Decimal(fill["reference_price"]), Decimal(fill["price"])
    quantity, fee = Decimal(fill["quantity"]), Decimal(fill["fee"])
    paid = price - reference if fill["side"] == "buy" else reference - price
    notional = quantity * reference
    return ((paid * quantity + fee) / notional * Decimal(10_000)) if notional else Decimal(0)


def _by(events: list[dict[str, Any]], kind: str, key: str) -> dict[str, dict[str, Any]]:
    return {e[key]: e for e in events if e["type"] == kind}


def track(account: PaperAccount, bars: pl.DataFrame, config: Config) -> Tracking:
    """Compare ``account`` with its replay on the simulated venue over ``bars`` (the same
    stored bars the account decided on)."""
    actual = account.ledger.events()
    decided = [e for e in actual if e["type"] == "decision"]
    if not decided:
        return Tracking([], [])
    terms = replace(account.terms(), venue="sim")
    # Replay to the last bar the account handled; a decision's price is the next bar's
    # open, so the replay needs the bars up to and including that next bar.
    last = datetime.fromisoformat(decided[-1]["bar"])
    upto = bars.filter(pl.col("open_time") <= last + terms.interval.duration)
    prices = {d["bar"]: d["price"] for d in decided if d.get("price") is not None}
    with tempfile.TemporaryDirectory() as tmp:
        model = PaperAccount.open(tmp, terms)
        run(
            model,
            upto,
            config,
            lambda at: (
                Decimal(prices[(at - terms.interval.duration).isoformat()])
                if (at - terms.interval.duration).isoformat() in prices
                else None
            ),
        )
        expected = model.ledger.events()
    marks, model_marks = _by(actual, "mark", "bar"), _by(expected, "mark", "bar")
    model_decisions = _by(expected, "decision", "bar")
    days = [
        DayTrack(
            datetime.fromisoformat(d["bar"]),
            Decimal(marks[d["bar"]]["equity"]),
            Decimal(model_marks[d["bar"]]["equity"]),
            Decimal(d["target"]),
            Decimal(model_decisions[d["bar"]]["target"]),
            bool(d.get("missed")),
        )
        for d in decided
        if d["bar"] in model_marks
    ]
    model_fills = _by(expected, "fill", "at")
    fills = [
        FillTrack(
            f["at"],
            terms.symbol,
            f["side"],
            _cost_bps(f),
            _cost_bps(model_fills[f["at"]]) if f["at"] in model_fills else None,
        )
        for f in actual
        if f["type"] == "fill"
    ]
    return Tracking(days, fills)


def track_basket(account: PaperAccount, panel: pl.DataFrame, config: Config) -> Tracking:
    """``track`` for a basket account: replay its strategy on the simulated venue over
    ``panel`` (the stored bars of every coin it can hold). A bar whose next open isn't
    stored yet isn't compared until it is."""
    actual = account.ledger.events()
    decided = [e for e in actual if e["type"] == "decision"]
    if not decided:
        return Tracking([], [])
    terms = replace(account.terms(), venue="sim")
    last = datetime.fromisoformat(decided[-1]["bar"])
    upto = panel.filter(pl.col("open_time") <= last + terms.interval.duration)
    with tempfile.TemporaryDirectory() as tmp:
        model = PaperAccount.open(tmp, terms)
        run_basket(model, upto, config)
        expected = model.ledger.events()
    marks, model_marks = _by(actual, "mark", "bar"), _by(expected, "mark", "bar")
    model_decisions = _by(expected, "decision", "bar")
    days = [
        DayTrack(
            datetime.fromisoformat(d["bar"]),
            Decimal(marks[d["bar"]]["equity"]),
            Decimal(model_marks[d["bar"]]["equity"]),
            d["targets"],
            model_decisions[d["bar"]]["targets"],
            bool(d.get("missed")),
        )
        for d in decided
        if d["bar"] in model_marks
    ]
    model_fills = {(e["at"], e["symbol"]): e for e in expected if e["type"] == "fill"}
    fills = [
        FillTrack(
            f["at"],
            f["symbol"],
            f["side"],
            _cost_bps(f),
            _cost_bps(model_fills[(f["at"], f["symbol"])])
            if (f["at"], f["symbol"]) in model_fills
            else None,
        )
        for f in actual
        if f["type"] == "fill"
    ]
    return Tracking(days, fills)


def format_tracking(name: str, t: Tracking) -> str:
    if not t.days:
        return f"{name}: nothing to track yet"

    def bps(x: Decimal | None) -> str:
        return "n/a" if x is None else f"{x:.1f} bps"

    last = t.days[-1]
    diff = t.difference
    lines = [
        f"Tracking  ·  {name}  ·  {len(t.days)} days to {last.bar:%Y-%m-%d}",
        "",
        f"  Equity            {last.equity:.2f} vs {last.expected:.2f} expected"
        + (f"  ({diff:+.2%})" if diff is not None else ""),
        f"  Decisions         {len(t.days) - t.decision_mismatches} of {len(t.days)} "
        "match the model"
        + ("" if not t.decision_mismatches else "  <- A BUG: the same code should decide the same"),
        f"  Missed days       {t.missed}",
        f"  Fills             {len(t.fills)}; cost {bps(t.mean_cost())} each vs "
        f"{bps(t.mean_cost(expected=True))} modelled",
    ]
    return "\n".join(lines)
