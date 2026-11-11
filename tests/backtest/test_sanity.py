"""The backtester's null test: a strategy with no information must make nothing before
costs and lose about its costs after them.

Prices follow a random walk whose arithmetic returns have mean zero, so price is a
martingale: no rule that decides from the past can expect to profit from it. Random
in-or-out weights are such a rule. Over many independent paths:

- before costs, the average result must be zero (within sampling error). A positive
  average would mean the engine leaks the future into fills or marks;
- after costs, the average result must equal minus the average costs paid. Anything
  else would mean costs are charged wrongly or there is a hidden edge or drag.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal
from statistics import fmean, stdev

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.metrics import compute_metrics
from kestrel.data.schema import Interval
from tests.backtest.helpers import FixedWeights, bars, config

PATHS = 200
BARS = 300


def _path(seed: int) -> tuple[list[float], list[float]]:
    rnd = random.Random(seed)
    closes, price = [], 100.0
    for _ in range(BARS):
        price *= 1 + rnd.gauss(0, 0.02)  # mean-zero arithmetic return: a martingale
        closes.append(price)
    # Decisions change on about one bar in ten, independently of prices.
    weights, w = [], 0.0
    for _ in range(BARS):
        if rnd.random() < 0.1:
            w = 1.0 - w
        weights.append(w)
    return closes, weights


def test_random_signals_earn_nothing_and_lose_their_costs() -> None:
    gross, net, cost_share = [], [], []
    for seed in range(PATHS):
        closes, weights = _path(seed)
        frame = bars([closes[0], *closes[:-1]], closes=closes)
        results = {}
        for with_costs in (False, True):
            r = run_backtest(
                FixedWeights(weights),
                frame,
                Interval.H4,
                config(costs=with_costs, band=0),
                Decimal(10_000),
            )
            results[with_costs] = r
        gross.append(float(results[False].final_equity) / 10_000 - 1)
        net.append(float(results[True].final_equity) / 10_000 - 1)
        cost_share.append(compute_metrics(results[True]).total_costs / 10_000)

    se_gross = stdev(gross) / math.sqrt(PATHS)
    assert abs(fmean(gross)) < 3 * se_gross, "random signals made money before costs"

    # Each path's net result differs from its gross by its costs (and their compounding),
    # so compare them pairwise: far less noisy than comparing the two averages.
    drag = [n - g for n, g in zip(net, gross, strict=True)]
    assert fmean(cost_share) > 0.01  # the test only means something if costs are material
    assert fmean(drag) < 0
    assert abs(fmean(drag) + fmean(cost_share)) < 0.1 * fmean(cost_share)
