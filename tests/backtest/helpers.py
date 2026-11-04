"""Small, hand-checkable inputs for engine and metric tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl

from kestrel.config import Config, parse_config
from kestrel.data.schema import conform

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def bars(
    opens: list[float],
    closes: list[float] | None = None,
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    start: datetime = T0,
    step: timedelta = timedelta(hours=4),
    quote_volume: float = 1e9,
) -> pl.DataFrame:
    closes = closes or opens
    n = len(opens)
    highs = highs or [max(o, c) for o, c in zip(opens, closes, strict=True)]
    lows = lows or [min(o, c) for o, c in zip(opens, closes, strict=True)]
    return conform(
        pl.DataFrame(
            {
                "open_time": [start + i * step for i in range(n)],
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": [1.0] * n,
                "quote_volume": [quote_volume] * n,
                "trade_count": [1] * n,
                "taker_buy_base_volume": [0.5] * n,
                "taker_buy_quote_volume": [quote_volume / 2] * n,
            }
        ).with_columns(pl.col("open_time").dt.cast_time_unit("us"))
    )


def config(
    *, costs: bool = True, band: float = 0.02, risk: float = 0.01, **instrument: Any
) -> Config:
    """A config with real or zero costs. Zero costs make equity exactly hand-checkable."""
    rules = {"step_size": "0.00001", "min_qty": "0.00001", "min_notional": "5", **instrument}
    return parse_config(
        {
            "account_currency": "USDT",
            "costs": {
                "taker_fee_bps": 10 if costs else 0,
                "half_spread_bps": 1 if costs else 0,
                "impact_coef_bps": 100 if costs else 0,
                "max_participation": 0.01,
            },
            "backtest": {
                "no_trade_band": band,
                "risk_per_trade": risk,
                "holdout_start": date(2025, 11, 1),
            },
            "instruments": {"BTCUSDT": rules},
        }
    )


class FixedWeights:
    """A strategy that replays a fixed list of weights (prefix-consistent, so no lookahead)."""

    def __init__(self, weights: list[float], symbol: str = "BTCUSDT") -> None:
        self.weights = weights
        self.symbol = symbol
        self.name = "fixed weights"

    def target_weights(self, frame: pl.DataFrame) -> pl.Series:
        return pl.Series("weight", self.weights[: frame.height], dtype=pl.Float64)


class FixedBrackets:
    """Replays fixed entries: ``entries`` maps bar index -> (stop, target)."""

    def __init__(self, entries: dict[int, tuple[float, float]], symbol: str = "BTCUSDT") -> None:
        self.entries = entries
        self.symbol = symbol
        self.name = "fixed brackets"

    def brackets(self, frame: pl.DataFrame) -> pl.DataFrame:
        n = frame.height
        return pl.DataFrame(
            {
                "enter": [i in self.entries for i in range(n)],
                "stop": [self.entries[i][0] if i in self.entries else None for i in range(n)],
                "target": [self.entries[i][1] if i in self.entries else None for i in range(n)],
            },
            schema={"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64},
        )
