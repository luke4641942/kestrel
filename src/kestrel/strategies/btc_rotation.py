"""BTC-regime momentum rotation (research; graduates G1 and G2 of the exploration
experiment's round 2, research/exploration/log.md).

Daily bars, decided at each Sunday close (UTC) and filled at Monday's open; positions only
change on Sundays. At a Sunday close:

1. Regime: BTC's close must be more than ``margin_pct``% above its ``btc_days``-day
   average. Otherwise hold nothing.
2. Eligible coins (from the strategy's fixed set): at least 365 days of unbroken history,
   and a known ``lookback_days`` return.
3. The top ``top_n`` by ``lookback_days`` return, each given ``1 / top_n`` of equity times
   min(1, ``target_vol_pct``% / its annualised volatility of the last ``vol_days`` daily
   log returns); the rest in cash.

BTC is in each set, both as the regime and as a coin that can be held. G1 holds the top 3
of 8 large coins; G2 the top 1 of BTC, ETH, SOL and BNB.
"""

from __future__ import annotations

import math

import polars as pl

from kestrel.strategies.basket import BY, DECISIONS, no_decisions, require_daily

BTC = "BTCUSDT"
LARGE_8 = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT",
)  # fmt: skip
LARGE_4 = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
MIN_HISTORY = 365


class BtcRegimeRotation:
    coins: tuple[str, ...] = LARGE_8
    label = "8 large coins"

    def __init__(
        self,
        symbol: str = "UNIVERSE",
        top_n: int = 3,
        lookback_days: int = 90,
        btc_days: int = 50,
        margin_pct: int = 3,
        vol_days: int = 30,
        target_vol_pct: int = 50,
    ) -> None:
        self.symbol = symbol
        self.top_n, self.lookback, self.btc_days = top_n, lookback_days, btc_days
        self.margin, self.vol_days, self.target = margin_pct / 100, vol_days, target_vol_pct / 100
        self.name = (
            f"BTC-regime rotation, top {top_n} of {self.label} by {lookback_days}d "
            f"(BTC > {btc_days}d +{margin_pct}%)"
        )

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        coins = panel.filter(pl.col("symbol").is_in(self.coins))
        if coins.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        close = pl.col("close")
        scored = coins.sort("open_time").with_columns(
            _r=(close / close.shift(self.lookback)).over(BY) - 1,
            _age=pl.int_range(1, pl.len() + 1).over(BY),
            _vol=close.log().diff().rolling_std(self.vol_days, min_samples=self.vol_days).over(BY)
            * math.sqrt(365),
        )
        btc = (
            scored.filter(pl.col("symbol") == BTC)
            .with_columns(_ma=close.rolling_mean(self.btc_days, min_samples=self.btc_days).over(BY))
            .select("open_time", _on=close > pl.col("_ma") * (1 + self.margin))
        )
        sundays = scored.filter(pl.col("open_time").dt.weekday() == 7).join(
            btc, on="open_time", how="left"
        )
        chosen = (
            sundays.filter(
                pl.col("_on").fill_null(False)
                & pl.col("_r").is_not_null()
                & (pl.col("_age") >= MIN_HISTORY)
                & pl.col("_vol").is_not_null()
                & (pl.col("_vol") > 0)
            )
            .sort(["open_time", "_r", "symbol"], descending=[False, True, False])
            .group_by("open_time", maintain_order=True)
            .head(self.top_n)
            .with_columns(
                weight=pl.min_horizontal(pl.lit(1.0), pl.lit(self.target) / pl.col("_vol"))
                / self.top_n
            )
            .select(list(DECISIONS))
        )
        days = sundays.select("open_time").unique()
        idle = days.join(chosen, on="open_time", how="anti").select(
            "open_time", symbol=pl.lit(None, pl.Utf8), weight=pl.lit(0.0)
        )
        out = pl.concat([chosen, idle]).select([pl.col(c).cast(t) for c, t in DECISIONS.items()])
        return out.sort("open_time", "symbol", nulls_last=True)


class BtcRegimeRotation4(BtcRegimeRotation):
    coins = LARGE_4
    label = "BTC/ETH/SOL/BNB"

    def __init__(
        self,
        symbol: str = "UNIVERSE",
        top_n: int = 1,
        lookback_days: int = 90,
        btc_days: int = 50,
        margin_pct: int = 3,
        vol_days: int = 30,
        target_vol_pct: int = 50,
    ) -> None:
        super().__init__(
            symbol, top_n, lookback_days, btc_days, margin_pct, vol_days, target_vol_pct
        )


# Confirmation baskets (exploration round 2, 2026-11-16): BTC plus the largest coins of the
# locked confirmation half, ranked by median daily quote volume over 2025-05 to 2025-10
# among those with a year of history by 2025-11-01 (volume only, no returns read).
CONFIRM_8 = (
    "BTCUSDT", "XRPUSDT", "SUIUSDT", "TRXUSDT", "ADAUSDT", "AVAXUSDT", "UNIUSDT", "ARBUSDT",
)  # fmt: skip
CONFIRM_4 = ("BTCUSDT", "XRPUSDT", "SUIUSDT", "TRXUSDT")


class BtcRegimeRotationConfirm8(BtcRegimeRotation):
    coins = CONFIRM_8
    label = "8 confirmation-half large coins"


class BtcRegimeRotationConfirm4(BtcRegimeRotation4):
    coins = CONFIRM_4
    label = "BTC and 3 confirmation-half large coins"
