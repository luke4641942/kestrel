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
    start: datetime = T0,
    step: timedelta = timedelta(hours=4),
    quote_volume: float = 1e9,
) -> pl.DataFrame:
    closes = closes or opens
    n = len(opens)
    return conform(
        pl.DataFrame(
            {
                "open_time": [start + i * step for i in range(n)],
                "open": opens,
                "high": [max(o, c) for o, c in zip(opens, closes, strict=True)],
                "low": [min(o, c) for o, c in zip(opens, closes, strict=True)],
                "close": closes,
                "volume": [1.0] * n,
                "quote_volume": [quote_volume] * n,
                "trade_count": [1] * n,
                "taker_buy_base_volume": [0.5] * n,
                "taker_buy_quote_volume": [quote_volume / 2] * n,
            }
        ).with_columns(pl.col("open_time").dt.cast_time_unit("us"))
    )


def config(*, costs: bool = True, band: float = 0.02, **instrument: Any) -> Config:
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
            "backtest": {"no_trade_band": band, "holdout_start": date(2025, 11, 1)},
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
