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
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

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
    train_days: int = 4 * 365
    restarts: int = 10
    seed: int = 0


PRESET = Preset()


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


def inputs(bars: pl.DataFrame, vol_days: int = PRESET.vol_days) -> pl.DataFrame:
    """Each day's inputs, null while the volatility window fills. Daily bars only: the
    model's persistence is in days."""
    if bars.height >= 2 and bars["open_time"].diff().median() != timedelta(days=1):
        raise ValueError("regimes need daily bars")
    if bars.height < 2:
        return bars.select("open_time").with_columns(
            pl.lit(None, pl.Float64).alias(f) for f in FEATURES
        )
    vol = realised_volatility(bars, vol_days)
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


def walk_forward(bars: pl.DataFrame, preset: Preset = PRESET) -> Regimes:
    """Refit at the start of each month on the ``train_days`` before it (once that much
    history exists), and filter the month with that model."""
    days = inputs(bars, preset.vol_days)
    ready = days.drop_nulls()
    times: list[datetime] = ready["open_time"].to_list()
    x = ready.select(FEATURES).to_numpy()
    probs: dict[datetime, list[float]] = {}
    refits: list[Refit] = []
    if times:
        for month in _month_starts(times[0], times[-1]):
            after = datetime(month.year + month.month // 12, month.month % 12 + 1, 1, tzinfo=UTC)
            since = month - timedelta(days=preset.train_days)
            train = [i for i, t in enumerate(times) if since <= t < month]
            test = [i for i, t in enumerate(times) if month <= t < after]
            if not test or len(train) < 0.9 * preset.train_days:
                continue
            model = fit(x[train], preset.states, restarts=preset.restarts, seed=preset.seed)
            span = x[train[0] : test[-1] + 1]
            alpha, both = filtered(model, span)
            for i, p in zip(test, alpha[len(train) :], strict=True):
                probs[times[i]] = [float(v) for v in p]
            oos = both - log_likelihood(model, x[train])
            refits.append(Refit(month, model, oos))
    all_times: list[datetime] = days["open_time"].to_list()
    return Regimes(all_times, [probs.get(t) for t in all_times], refits)


# ---------- describing the states ----------


@dataclass(frozen=True)
class StateSummary:
    state: int
    share: float
    """Share of filtered days on which this was the most likely state."""
    mean_return: float | None
    """Average daily log return on those days."""
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
    returns = inputs(bars)["return"].to_list()
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
                mean_return=float(np.mean(rs)) if rs else None,
                volatility=float(np.std(rs, ddof=1) * math.sqrt(365)) if len(rs) > 1 else None,
                average_run=float(np.mean(runs[k])) if k in runs else None,
                expected_days=float(latest.expected_days()[k]),
            )
        )
    return out


def format_regimes(
    regimes: Regimes, bars: pl.DataFrame, symbol: str, preset: Preset = PRESET
) -> str:
    """The CLI's report: each state as the model saw it, and the latest switching odds."""
    states = describe(regimes, bars)
    if not states:
        return f"Regimes  ·  {symbol} 1d  ·  not enough history for a first fit"

    def pct(x: float | None, digits: int = 0) -> str:
        return "n/a" if x is None else f"{x:+.{digits}%}" if digits else f"{x:.0%}"

    latest = regimes.refits[-1]
    lines = [
        f"Regimes  ·  {symbol} 1d  ·  {len(regimes.refits)} monthly refits from "
        f"{regimes.refits[0].month:%Y-%m}, each on the {preset.train_days} days before it",
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
        f"  Switching odds from the {latest.month:%Y-%m} model (row: today, column: tomorrow)",
        *("  " + f"{i:<7}" + " ".join(f"{p:>6.1%}" for p in row) for i, row in enumerate(trans)),
        "",
        "  States are sorted calmest first. Every day's state is what the model believed that",
        "  day, from a model fitted only on earlier data.",
        f"  Out-of-sample log-likelihood: {regimes.oos_log_likelihood:.1f} "
        "(higher is better; compare inputs by this, never by trading results).",
    ]
    return "\n".join(lines)
