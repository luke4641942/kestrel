"""Market regimes from a Gaussian hidden Markov model, using only the past.

The market is assumed to move between a few hidden states (calm, choppy, falling...).
Each state draws each day's inputs from its own normal distribution, and states switch
with fixed daily probabilities (the transition matrix), so they tend to persist. Fitting
(Baum-Welch, a form of expectation-maximisation) finds the distributions and switching
probabilities that best explain the training data.

**No lookahead.** Two common mistakes make HMM backtests look far better than they are:

- *Smoothed states.* ``predict`` and ``predict_proba`` in most libraries use the whole
  sequence, so a day's state depends on later days. Here every probability is
  *filtered*: P(state at t | inputs up to t), from the forward pass alone.
- *Fitting on the future.* A model fitted once on all the data has seen it. Here the model
  is refitted at the start of every month on the ``train_days`` before it, then filters
  that month's days. Data from the month or later never touches the fit.

``tests/test_regimes.py`` checks the forward pass against brute-force enumeration and that
truncating the history never changes an earlier probability.

**Labels.** A fit numbers its states arbitrarily, and the numbering can change between
refits. After every fit the states are sorted by their average volatility input, so state
0 is always the calmest.

**Inputs.** By default each day's log return and the log of its 20-day realised volatility
(annualised), both known at the day's close. The log makes volatility closer to normal,
which the Gaussian states assume. Other inputs can be compared by out-of-sample
likelihood (``Regimes.oos_log_likelihood``): how probable the model found each next month
before seeing it. That judges the model, not a trading result.

The fixed preset (``PRESET``) is what the dashboard shows; changing it for a strategy
means a research note, like any other setting.

**4h bars** (``FAST_PRESET``): the same model shape on 4-hour bars, the 4h model of
findings 23 (20-bar volatility, a 1-year window, 3 restarts). It is stored apart
(``RegimeStore(data_dir, Interval.H4)``) and read by the ``regime_volatile_4h`` feature;
the Regimes page shows only the daily fit.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from kestrel.atomic import write_atomically
from kestrel.indicators import realised_volatility

FEATURES = ("return", "log_volatility")
VOLATILITY_COLUMN = 1
"""The input the states are sorted by."""

_LOG_2PI = math.log(2 * math.pi)
_VARIANCE_FLOOR = 1e-4
"""Smallest variance, as a share of the input's overall variance: stops a state from
collapsing onto a single day."""


@dataclass(frozen=True)
class Preset:
    states: int = 3
    vol_days: int = 20
    """Bars of returns in the volatility input (days, on the daily preset)."""
    train_days: int = 4 * 365
    restarts: int = 10
    seed: int = 0
    bar_hours: int = 24

    @property
    def bar(self) -> timedelta:
        return timedelta(hours=self.bar_hours)

    @property
    def bars_per_day(self) -> float:
        return 24 / self.bar_hours


PRESET = Preset()
FAST_PRESET = Preset(states=3, vol_days=20, train_days=365, restarts=3, seed=0, bar_hours=4)
"""Findings 23's 4h model, fixed before it was run (2026-11-22)."""


@dataclass(frozen=True)
class Hmm:
    start: np.ndarray
    """(k,) probability of each state on the first day."""
    trans: np.ndarray
    """(k, k) trans[i, j] = P(state j tomorrow | state i today)."""
    means: np.ndarray
    """(k, d) each state's mean of each input."""
    variances: np.ndarray
    """(k, d) each state's variance of each input (inputs independent within a state)."""
    log_likelihood: float = float("nan")
    """Of the data it was fitted on."""

    @property
    def states(self) -> int:
        return len(self.start)

    def expected_days(self) -> np.ndarray:
        """How long a stay in each state lasts on average: 1 / (1 - P(stay))."""
        stay = np.diag(self.trans)
        days: np.ndarray = 1 / np.maximum(1 - stay, 1e-12)
        return days


# ---------- the forward and backward passes ----------


def _log_emissions(model: Hmm, x: np.ndarray) -> np.ndarray:
    """(n, k) log density of each day's inputs under each state."""
    diff = x[:, None, :] - model.means[None, :, :]
    out: np.ndarray = -0.5 * (
        _LOG_2PI * x.shape[1]
        + np.log(model.variances).sum(axis=1)[None, :]
        + (diff**2 / model.variances[None, :, :]).sum(axis=2)
    )
    return out


def _forward(model: Hmm, log_b: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Scaled forward pass. Returns the filtered probabilities (n, k), the emission
    likelihoods rescaled per day (n, k), and the log-likelihood of the whole sequence."""
    shift = log_b.max(axis=1, keepdims=True)
    b = np.exp(log_b - shift)
    alpha = np.empty_like(b)
    scale = np.empty(len(b))
    a = model.start * b[0]
    for t in range(len(b)):
        if t:
            a = (alpha[t - 1] @ model.trans) * b[t]
        scale[t] = a.sum()
        alpha[t] = a / scale[t]
    return alpha, b, float(np.log(scale).sum() + shift.sum())


def filtered(model: Hmm, x: np.ndarray) -> tuple[np.ndarray, float]:
    """P(state at t | inputs up to and including t) for every day, and the log-likelihood
    of the inputs."""
    alpha, _, log_l = _forward(model, _log_emissions(model, x))
    return alpha, log_l


def log_likelihood(model: Hmm, x: np.ndarray) -> float:
    return filtered(model, x)[1]


def predictive_log_likelihood(
    model: Hmm, x: np.ndarray, start: int, columns: list[int] | None = None
) -> float:
    """The sum, over days ``t >= start``, of log p(day t's ``columns`` | every input before
    t): each day predicted one step ahead, beliefs updated on all of ``x``'s inputs.

    With every column this is log p(x[start:] | x[:start]), the out-of-sample score. With
    only some, it lets models with different extra inputs be compared fairly: each is scored
    on how well it predicts the same shared inputs, and the extra ones count only through
    what they teach the filter. Raw likelihoods over different inputs aren't comparable."""
    cols = list(range(x.shape[1])) if columns is None else columns
    full = _log_emissions(model, x)
    scored = _log_emissions(
        replace(model, means=model.means[:, cols], variances=model.variances[:, cols]),
        x[:, cols],
    )
    total = 0.0
    belief = model.start
    for t in range(len(x)):
        prior = belief if t == 0 else belief @ model.trans
        if t >= start:
            top = scored[t].max()
            total += float(top + np.log(prior @ np.exp(scored[t] - top)))
        top = full[t].max()
        posterior = prior * np.exp(full[t] - top)
        belief = posterior / posterior.sum()
    return total


# ---------- fitting ----------


def _initial(x: np.ndarray, k: int, rng: np.random.Generator) -> Hmm:
    means = x[rng.choice(len(x), size=k, replace=False)]
    variances = np.tile(x.var(axis=0), (k, 1))
    trans = np.full((k, k), 0.1 / (k - 1)) if k > 1 else np.ones((1, 1))
    np.fill_diagonal(trans, 0.9 if k > 1 else 1.0)
    return Hmm(np.full(k, 1 / k), trans, means, variances)


def _em(model: Hmm, x: np.ndarray, iterations: int, tol: float) -> tuple[Hmm, list[float]]:
    """Baum-Welch from ``model``; returns the fit and its log-likelihood at each step."""
    floor = _VARIANCE_FLOOR * x.var(axis=0)
    history: list[float] = []
    for _ in range(iterations):
        alpha, b, log_l = _forward(model, _log_emissions(model, x))
        history.append(log_l)
        if len(history) > 1 and history[-1] - history[-2] < tol:
            break
        # Backward pass, scaled to match the forward one; then every day's expected
        # transitions at once, each day's pair normalised to sum to one.
        beta = np.ones_like(alpha)
        for t in range(len(x) - 2, -1, -1):
            beta[t] = model.trans @ (b[t + 1] * beta[t + 1])
            beta[t] /= alpha[t] @ beta[t]
        nxt = b[1:] * beta[1:]
        pairs = alpha[:-1, :, None] * model.trans[None, :, :] * nxt[:, None, :]
        xi = (pairs / pairs.sum(axis=(1, 2), keepdims=True)).sum(axis=0)
        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True)
        weight = gamma.sum(axis=0)[:, None]
        means = gamma.T @ x / weight
        variances = np.maximum(gamma.T @ x**2 / weight - means**2, floor)
        trans = xi / xi.sum(axis=1, keepdims=True)
        model = Hmm(gamma[0], trans, means, variances)
    return replace(model, log_likelihood=history[-1]), history


def relabel(model: Hmm) -> Hmm:
    """States sorted by their mean volatility input, calmest first."""
    order = np.argsort(model.means[:, VOLATILITY_COLUMN], kind="stable")
    return Hmm(
        model.start[order],
        model.trans[np.ix_(order, order)],
        model.means[order],
        model.variances[order],
        model.log_likelihood,
    )


def fit(
    x: np.ndarray,
    states: int,
    *,
    restarts: int = 10,
    seed: int = 0,
    iterations: int = 200,
    tol: float = 1e-3,
) -> Hmm:
    """The best of ``restarts`` fits from random starting points (EM finds a local best,
    so one start can get stuck), relabelled calmest first. Deterministic for a seed.
    Each fit stops when an iteration adds less than ``tol`` to the log-likelihood."""
    if len(x) <= states * x.shape[1] * 3:
        raise ValueError(f"need more than {states * x.shape[1] * 3} days to fit {states} states")
    rng = np.random.default_rng(seed)
    best: Hmm | None = None
    for _ in range(restarts):
        candidate, _ = _em(_initial(x, states, rng), x, iterations, tol)
        if best is None or candidate.log_likelihood > best.log_likelihood:
            best = candidate
    assert best is not None
    return relabel(best)


# ---------- inputs ----------


def inputs(
    bars: pl.DataFrame, vol_days: int = PRESET.vol_days, bar: timedelta = PRESET.bar
) -> pl.DataFrame:
    """Each bar's inputs, null while the volatility window fills. The bars must be the
    preset's size: the model's persistence is in bars."""
    if bars.height >= 2 and bars["open_time"].diff().median() != bar:
        raise ValueError(f"regimes need {'daily' if bar == timedelta(days=1) else bar} bars")
    if bars.height < 2:
        return bars.select("open_time").with_columns(
            pl.lit(None, pl.Float64).alias(f) for f in FEATURES
        )
    if bar == timedelta(days=1):
        vol = realised_volatility(bars, vol_days)
    else:  # a window of vol_days *bars*, as findings 23's 4h model (it reacts in hours)
        c = bars["close"].cast(pl.Float64)
        per_year = timedelta(days=365) / bar
        vol = (c / c.shift(1)).log().rolling_std(vol_days) * math.sqrt(per_year)
    return bars.select(
        "open_time",
        (pl.col("close") / pl.col("close").shift(1)).log().alias("return"),
        # A flat window has no volatility to take the log of: no input that day.
        pl.when(pl.Series(vol) > 0).then(pl.Series(vol).log()).alias("log_volatility"),
    )


# ---------- walking forward ----------


@dataclass(frozen=True)
class Refit:
    month: datetime
    """First day the model filtered."""
    model: Hmm
    oos_log_likelihood: float
    """Log-likelihood of the month's inputs given the training window, before seeing them."""


@dataclass(frozen=True)
class Regimes:
    open_time: list[datetime]
    probabilities: list[list[float] | None]
    """Per day, P(each state) from that day's model and inputs up to that day; None before
    the first fit."""
    refits: list[Refit]
    preset: Preset = PRESET
    """The settings the fit used."""

    @property
    def oos_log_likelihood(self) -> float:
        """Total over every month: the score to compare inputs by."""
        return sum(r.oos_log_likelihood for r in self.refits)

    @property
    def states(self) -> list[int | None]:
        """The most likely state each day, as believed that day."""
        return [None if p is None else int(np.argmax(p)) for p in self.probabilities]


def _month_starts(first: datetime, last: datetime) -> list[datetime]:
    out, m = [], datetime(first.year, first.month, 1, tzinfo=UTC)
    while m <= last:
        out.append(m)
        m = datetime(m.year + m.month // 12, m.month % 12 + 1, 1, tzinfo=UTC)
    return out


def walk_forward(
    bars: pl.DataFrame,
    preset: Preset = PRESET,
    on_month: Callable[[int, int], None] | None = None,
) -> Regimes:
    """Refit at the start of each month on the ``train_days`` before it (once that much
    history exists), and filter the month with that model. ``on_month(done, total)`` is
    called before each month, for progress; it may raise to stop the fit."""
    days = inputs(bars, preset.vol_days, preset.bar)
    ready = days.drop_nulls()
    times: list[datetime] = ready["open_time"].to_list()
    x = ready.select(FEATURES).to_numpy()
    probs: dict[datetime, list[float]] = {}
    refits: list[Refit] = []
    months = _month_starts(times[0], times[-1]) if times else []
    for done, month in enumerate(months):
        if on_month is not None:
            on_month(done, len(months))
        after = datetime(month.year + month.month // 12, month.month % 12 + 1, 1, tzinfo=UTC)
        since = month - timedelta(days=preset.train_days)
        train = [i for i, t in enumerate(times) if since <= t < month]
        test = [i for i, t in enumerate(times) if month <= t < after]
        if not test or len(train) < 0.9 * preset.train_days * preset.bars_per_day:
            continue
        model = fit(x[train], preset.states, restarts=preset.restarts, seed=preset.seed)
        span = x[train[0] : test[-1] + 1]
        alpha, both = filtered(model, span)
        for i, p in zip(test, alpha[len(train) :], strict=True):
            probs[times[i]] = [float(v) for v in p]
        oos = both - log_likelihood(model, x[train])
        refits.append(Refit(month, model, oos))
    all_times: list[datetime] = days["open_time"].to_list()
    return Regimes(all_times, [probs.get(t) for t in all_times], refits, preset)


# ---------- describing the states ----------


@dataclass(frozen=True)
class StateSummary:
    state: int
    share: float
    """Share of filtered days on which this was the most likely state."""
    mean_return: float | None
    """Average log return per day (per bar x bars a day) in this state."""
    volatility: float | None
    """Annualised standard deviation of daily returns on those days."""
    average_run: float | None
    """Average length, in days, of an unbroken spell in this state."""
    expected_days: float
    """From the latest model's transition matrix."""


def describe(regimes: Regimes, bars: pl.DataFrame) -> list[StateSummary]:
    """What each state has looked like, as the model saw it at the time."""
    if not regimes.refits:
        return []
    latest = regimes.refits[-1].model
    preset = regimes.preset
    returns = inputs(bars, preset.vol_days, preset.bar)["return"].to_list()
    labelled = [(s, r) for s, r in zip(regimes.states, returns, strict=True) if s is not None]
    runs: dict[int, list[int]] = {}
    previous, length = None, 0
    for s, _ in labelled:
        if s == previous:
            length += 1
            continue
        if previous is not None:
            runs.setdefault(previous, []).append(length)
        previous, length = s, 1
    if previous is not None:
        runs.setdefault(previous, []).append(length)
    out = []
    for k in range(latest.states):
        rs = [r for s, r in labelled if s == k and r is not None]
        out.append(
            StateSummary(
                state=k,
                share=len([1 for s, _ in labelled if s == k]) / len(labelled),
                mean_return=float(np.mean(rs)) * preset.bars_per_day if rs else None,
                volatility=float(np.std(rs, ddof=1) * math.sqrt(365 * preset.bars_per_day))
                if len(rs) > 1
                else None,
                average_run=float(np.mean(runs[k])) / preset.bars_per_day if k in runs else None,
                expected_days=float(latest.expected_days()[k]) / preset.bars_per_day,
            )
        )
    return out


def format_regimes(regimes: Regimes, bars: pl.DataFrame, symbol: str) -> str:
    """The CLI's report: each state as the model saw it, and the latest switching odds."""
    states = describe(regimes, bars)
    p = regimes.preset
    size = "1d" if p.bar_hours == 24 else f"{p.bar_hours}h"
    if not states:
        return f"Regimes  ·  {symbol} {size}  ·  not enough history for a first fit"

    def pct(x: float | None, digits: int = 0) -> str:
        return "n/a" if x is None else f"{x:+.{digits}%}" if digits else f"{x:.0%}"

    latest = regimes.refits[-1]
    lines = [
        f"Regimes  ·  {symbol} {size}  ·  {len(regimes.refits)} monthly refits from "
        f"{regimes.refits[0].month:%Y-%m}, each on the {regimes.preset.train_days} days before it",
        "",
        f"  {'state':<7} {'share':>6} {'return/day':>11} {'volatility':>11} "
        f"{'avg spell':>10} {'expected':>9}",
    ]
    for s in states:
        spell = "n/a" if s.average_run is None else f"{s.average_run:.0f} days"
        lines.append(
            f"  {s.state:<7} {pct(s.share):>6} {pct(s.mean_return, 2):>11} "
            f"{pct(s.volatility):>11} {spell:>10} {s.expected_days:>6.0f} days"
        )
    trans = latest.model.trans
    lines += [
        "",
        f"  Switching odds from the {latest.month:%Y-%m} model "
        + (
            "(row: today, column: tomorrow)"
            if size == "1d"
            else "(row: this bar, column: the next)"
        ),
        *("  " + f"{i:<7}" + " ".join(f"{p:>6.1%}" for p in row) for i, row in enumerate(trans)),
        "",
        "  States are sorted calmest first. Every bar's state is what the model believed at",
        "  its close, from a model fitted only on earlier data. Returns are per day.",
        f"  Out-of-sample log-likelihood: {regimes.oos_log_likelihood:.1f} "
        "(higher is better; compare inputs by this, never by trading results).",
    ]
    return "\n".join(lines)


# ---------- storage ----------


class RegimeStore:
    """The latest fit per market, as the dashboard shows it:

        <data>/regimes/<SYMBOL>.json        (daily, the preset)
        <data>/regimes/4h/<SYMBOL>.json     (4h, FAST_PRESET; not on the dashboard)

    Refitting a market replaces its file (written atomically). A fit is a description,
    not a trial: nothing here is traded or scored, so nothing is counted."""

    def __init__(self, data_dir: Path | str, interval: str = "1d") -> None:
        if interval not in ("1d", "4h"):
            raise ValueError("regimes are fitted on 1d or 4h bars")
        self.root = Path(data_dir) / "regimes"
        if interval != "1d":
            self.root = self.root / interval

    def save(self, symbol: str, regimes: Regimes, bars: pl.DataFrame) -> dict[str, Any]:
        latest = regimes.refits[-1].model if regimes.refits else None
        doc: dict[str, Any] = {
            "symbol": symbol,
            "created_at": datetime.now(UTC).isoformat(),
            "preset": asdict(regimes.preset),
            "first": regimes.open_time[0].isoformat() if regimes.open_time else None,
            "last": regimes.open_time[-1].isoformat() if regimes.open_time else None,
            "time": [int(t.timestamp()) for t in regimes.open_time],
            "close": bars["close"].to_list(),
            "probabilities": regimes.probabilities,
            "state": regimes.states,
            "states": [asdict(s) for s in describe(regimes, bars)],
            "transitions": latest.trans.tolist() if latest is not None else None,
            "refits": [
                {"month": r.month.isoformat(), "oos_log_likelihood": r.oos_log_likelihood}
                for r in regimes.refits
            ],
            "oos_log_likelihood": regimes.oos_log_likelihood,
        }
        self.root.mkdir(parents=True, exist_ok=True)
        write_atomically(self.root / f"{symbol}.json", lambda tmp: tmp.write_text(json.dumps(doc)))
        return doc

    def load(self, symbol: str) -> dict[str, Any]:
        path = self.root / f"{symbol}.json"
        if not path.exists():
            raise KeyError(symbol)
        doc: dict[str, Any] = json.loads(path.read_text())
        return doc

    def summaries(self) -> list[dict[str, Any]]:
        """Every saved fit without its daily series, newest first."""
        out = []
        for path in sorted(self.root.glob("*.json")):
            doc = json.loads(path.read_text())
            out.append(
                {
                    "symbol": doc["symbol"],
                    "created_at": doc["created_at"],
                    "first": doc["first"],
                    "last": doc["last"],
                    "refits": len(doc["refits"]),
                }
            )
        return sorted(out, key=lambda d: d["created_at"], reverse=True)
