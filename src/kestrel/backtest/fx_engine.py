"""The forex backtest engine (P6): signed, leveraged weights across pairs, in AUD.

Its own engine rather than a mode of the crypto one: the crypto engine is spot (cash,
whole coins, exchange minimums, long only) and the paper accounts depend on it, so
shorting and leverage don't go anywhere near it. What the two share is the model, and a
test holds them to it (``tests/backtest/test_fx_engine.py``: long-only, unlevered, the
same bars and spread, the same result within the crypto parity tolerances).

Timing (no lookahead), as in every Kestrel backtest:
    day t closes (17:00 New York)  -> the strategy's weights for day t are known
    day t+1 opens                  -> orders fill at its open: buys at the ask, sells at
                                      the bid (the spread is in the data, not assumed)
    day t+1 closes                 -> positions are marked at the mid close, and the
                                      night's financing is charged or earned

**Weights** are per pair, signed (negative is short), with the sum of |weights| at most
``[fx] max_gross_leverage``. A weight is the position's notional in account currency as a
share of equity. A pair whose change is within the no-trade band isn't traded.

**Account currency.** A pair's profit or loss is in its quote currency (JPY for USDJPY)
and is converted to the account's (AUD) at the mid close, through the pairs themselves
(AUD per USD is 1 / AUDUSD; per JPY, that over USDJPY). Orders are sized at the previous
close's conversion, the last one known when the order is placed.

**Financing.** A position held through a close pays or earns, per calendar day until the
next close (so Friday to Monday is three days, as brokers charge over the weekend):
    long base:   notional x (r_base - r_quote - markup) / 365
    short base:  notional x (r_quote - r_base - markup) / 365
with ``r`` each currency's annual short-term rate from ``rates`` and the broker's markup
from ``[fx] financing_markup_pct``. The rates are an input (``RateFn``), never defaulted:
a backtest states where its rates came from.

**Missing days.** A pair with no candle on a day (a holiday) isn't traded or marked that
day; its last close stands. A strategy's weight for a pair with no candle is ignored.

Data from the forex holdout (``[fx] holdout_start``) is refused unless the caller allows
it explicitly. If equity reaches zero the run stops there (``wiped_out``): a leveraged
account can be lost, and the result says so rather than going negative.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import polars as pl

from kestrel.config import Config, FxConfig
from kestrel.provenance import CodeVersion, code_version

RateFn = Callable[[str, datetime], float]
"""A currency's annual short-term interest rate (0.05 is 5%) known at a time."""


def zero_rates(currency: str, at: datetime) -> float:
    """No interest anywhere: for tests and for a run that says so."""
    return 0.0


class FxEngineError(ValueError):
    """Inputs the engine refuses (holdout data, impossible weights, a missing pair)."""


@dataclass(frozen=True)
class FxFill:
    time: datetime
    pair: str
    units: float
    """Base-currency units bought (positive) or sold (negative)."""
    price: float
    """The fill: the ask for a buy, the bid for a sell."""
    spread_cost: float
    """In account currency: the distance from the mid, times the units."""
    notional: float = 0.0
    """In account currency, at the mid open."""


@dataclass
class FxResult:
    strategy: str
    pairs: tuple[str, ...]
    initial_equity: float
    account_currency: str
    equity: pl.DataFrame = field(default_factory=pl.DataFrame)
    """Per day: close_time, equity, gross (sum of |weights| held) and net exposure."""
    fills: list[FxFill] = field(default_factory=list)
    spread_cost: float = 0.0
    financing: float = 0.0
    """Net financing in account currency: positive means it earned more than it paid."""
    notional_nights: float = 0.0
    """Sum over nights of the notional held (account currency): what a different markup
    would change. Each extra 1% a year of markup costs about notional_nights / 365 / 100."""
    band_skips: int = 0
    wiped_out: datetime | None = None
    used_holdout: bool = False
    config_fingerprint: str = ""
    code: CodeVersion | None = None

    @property
    def final_equity(self) -> float:
        return float(self.equity["equity"][-1]) if self.equity.height else self.initial_equity


def account_per_unit(currency: str, account: str, mids: dict[str, float]) -> float | None:
    """Account currency per unit of ``currency``, through the pairs' mids (None if the
    pairs can't connect them). Every route goes through USD, as the 10 pairs do."""

    def usd(c: str) -> float | None:
        if c == "USD":
            return 1.0
        if f"{c}USD" in mids:
            return mids[f"{c}USD"]
        if f"USD{c}" in mids:
            return 1 / mids[f"USD{c}"]
        return None

    if currency == account:
        return 1.0
    a, c = usd(account), usd(currency)
    return None if a is None or c is None else c / a


def _calendar(bars: dict[str, pl.DataFrame]) -> list[datetime]:
    days: set[datetime] = set()
    for frame in bars.values():
        days.update(frame["open_time"].to_list())
    return sorted(days)


def run_fx(
    name: str,
    bars: dict[str, pl.DataFrame],
    weights: pl.DataFrame,
    fx_config: FxConfig,
    band: float,
    rates: RateFn,
    initial_equity: float,
    *,
    conversion: dict[str, pl.DataFrame] | None = None,
    config: Config | None = None,
    allow_holdout: bool = False,
) -> FxResult:
    """Trade ``weights`` (open_time plus one column per pair, decided at each close) over
    ``bars`` (kestrel.data.fx frames, weekend candles already dropped).

    ``conversion`` holds the pairs used only to convert to the account currency (every
    traded pair is used too); ``band`` is the no-trade band, a share of equity."""
    initial_equity = float(initial_equity)
    pairs = tuple(sorted(bars))
    for pair in pairs:
        if pair not in weights.columns:
            raise FxEngineError(f"no weights for {pair}")
    used_holdout = False
    for pair, frame in bars.items():
        if frame.height and frame["open_time"].max() >= fx_config.holdout_start:  # type: ignore[operator]
            if not allow_holdout:
                raise FxEngineError(
                    f"{pair} reaches the forex holdout (from {fx_config.holdout_start:%Y-%m-%d})"
                )
            used_holdout = True
    account = fx_config.account_currency
    markup = float(fx_config.financing_markup_pct) / 100
    cap = float(fx_config.max_gross_leverage)

    days = _calendar(bars)
    by_day = {
        pair: {r["open_time"]: r for r in frame.iter_rows(named=True)}
        for pair, frame in bars.items()
    }
    conv_rows = {
        pair: {r["open_time"]: r["close"] for r in frame.iter_rows(named=True)}
        for pair, frame in (conversion or {}).items()
    }
    targets = {r["open_time"]: r for r in weights.iter_rows(named=True)}

    result = FxResult(
        name,
        pairs,
        initial_equity,
        account,
        used_holdout=used_holdout,
        config_fingerprint=config.fingerprint() if config else "",
        code=code_version(),
    )
    units = dict.fromkeys(pairs, 0.0)
    last_close: dict[str, float] = {}
    mids: dict[str, float] = {}  # latest mid close of every pair seen, for conversion
    equity = initial_equity
    previous_target: dict[str, float] = {}
    rows: list[tuple[datetime, float, float, float]] = []

    def to_account(pair: str) -> float:
        factor = account_per_unit(pair[3:], account, mids)
        if factor is None:
            raise FxEngineError(f"can't convert {pair[3:]} to {account} on the pairs given")
        return factor

    for i, day in enumerate(days):
        decided = targets.get(days[i - 1]) if i else None
        # 1. Overnight: from each pair's last close to today's open, at the last conversion.
        for pair in pairs:
            bar = by_day[pair].get(day)
            if bar is not None and pair in last_close and units[pair]:
                equity += units[pair] * (bar["open"] - last_close[pair]) * to_account(pair)
        # 2. Trade at the open towards yesterday's decision.
        if decided is not None and equity > 0:
            wanted = {p: float(decided[p] or 0.0) for p in pairs}
            if not all(math.isfinite(w) for w in wanted.values()):
                raise FxEngineError(f"a weight on {days[i - 1]:%Y-%m-%d} isn't a number")
            gross = sum(abs(w) for w in wanted.values())
            if gross > cap + 1e-9:
                raise FxEngineError(
                    f"weights on {days[i - 1]:%Y-%m-%d} ask for {gross:.2f}x, over the {cap:g}x cap"
                )
            for pair in pairs:
                bar = by_day[pair].get(day)
                if bar is None:  # a holiday for this pair: its weight waits
                    continue
                per_unit = bar["open"] * to_account(pair)
                current = units[pair] * per_unit / equity
                target = wanted[pair]
                if abs(target - current) <= band:
                    if target != previous_target.get(pair, 0.0) and target != current:
                        result.band_skips += 1
                    previous_target[pair] = target
                    continue
                previous_target[pair] = target
                delta = target * equity / per_unit - units[pair]
                price = bar["ask_open"] if delta > 0 else bar["bid_open"]
                cost = abs(delta) * abs(price - bar["open"]) * to_account(pair)
                units[pair] += delta
                equity -= cost
                result.spread_cost += cost
                result.fills.append(FxFill(day, pair, delta, price, cost, abs(delta) * per_unit))
        # 3. The day: open to close, then convert at the close.
        for pair in pairs:
            bar = by_day[pair].get(day)
            if bar is not None:
                mids[pair] = bar["close"]
        for pair, closes in conv_rows.items():
            if day in closes:
                mids[pair] = closes[day]
        for pair in pairs:
            bar = by_day[pair].get(day)
            if bar is None:
                continue
            if units[pair]:
                equity += units[pair] * (bar["close"] - bar["open"]) * to_account(pair)
            last_close[pair] = bar["close"]
        # 4. Financing for the night(s) until the next close.
        nights = (days[i + 1] - day).days if i + 1 < len(days) else 1
        for pair in pairs:
            if not units[pair] or pair not in last_close:
                continue
            notional = abs(units[pair]) * last_close[pair] * to_account(pair)
            base, quote = rates(pair[:3], day), rates(pair[3:], day)
            carry = (base - quote) if units[pair] > 0 else (quote - base)
            earned = notional * (carry - markup) / 365 * nights
            result.notional_nights += notional * nights
            equity += earned
            result.financing += earned
        held = {p: units[p] * last_close[p] * to_account(p) for p in pairs if units[p]}
        if equity <= 0:
            result.wiped_out = day
            rows.append((day + timedelta(days=1), 0.0, 0.0, 0.0))
            break
        gross = sum(abs(v) for v in held.values()) / equity
        net = sum(held.values()) / equity
        rows.append((day + timedelta(days=1), equity, gross, net))

    result.equity = pl.DataFrame(
        {
            "close_time": [r[0] for r in rows],
            "equity": [r[1] for r in rows],
            "gross": [r[2] for r in rows],
            "net": [r[3] for r in rows],
        },
        schema={
            "close_time": pl.Datetime("us", "UTC"),
            "equity": pl.Float64,
            "gross": pl.Float64,
            "net": pl.Float64,
        },
    )
    return result
