"""Altcoin portfolio on the BTC regime, with exit rules (research; note
altcoin-portfolio-on-the-btc-regime-with-exit).

G1's weekly rule (kestrel.strategies.btc_rotation) on a point-in-time universe instead of
a fixed list, with two exit rules. At each Sunday close (UTC), traded at Monday's open:

1. Regime: BTC's close more than ``margin_pct``% above its ``btc_days``-day average;
   otherwise hold nothing.
2. Eligible coins, from what is known at that close: every coin on the panel except BTC,
   ETH, SOL and the coins in ``SPENT`` (their holdout years are used); at least
   ``min_history`` days of unbroken history; a median daily quote volume of at least
   ``min_volume_m`` million USDT over the last 30 days; its close above its close
   ``coin_days`` days earlier; a known ``lookback_days`` return and volatility; no
   delisting announced; and not within ``perp_exit_days`` days of its first perp.
3. The top ``top_n`` by ``lookback_days`` return, each at 1/top_n x min(1,
   ``target_vol_pct``% / its annualised volatility of the last ``vol_days`` daily log
   returns); the rest in cash.

Exit rules, every day: a held pick whose delisting is announced, or whose first perp is
within ``perp_exit_days`` days, is sold at the next open (the other picks are kept as
held). The panel carries ``delisting_announced`` and ``perp_days``
(kestrel.data.coin_events). BTC must be on the panel (the regime); it is never picked.
"""

from __future__ import annotations

import math

import polars as pl

from kestrel.data.coin_events import DELISTING, PERP_DAYS
from kestrel.strategies.basket import BY, DECISIONS, no_decisions, require_daily

BTC = "BTCUSDT"
NEVER = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
"""Holdout spent: never picked (BTC is still read for the regime)."""
SPENT = ("XRPUSDT", "SUIUSDT", "TRXUSDT", "ADAUSDT", "AVAXUSDT", "UNIUSDT", "ARBUSDT")
"""Holdout year used by the rotation graduates' confirmation (findings 15)."""


class AltcoinRegimeRotation:
    def __init__(
        self,
        symbol: str = "UNIVERSE",
        top_n: int = 3,
        lookback_days: int = 90,
        btc_days: int = 50,
        margin_pct: int = 3,
        coin_days: int = 30,
        vol_days: int = 30,
        target_vol_pct: int = 50,
        min_volume_m: int = 20,
        min_history: int = 365,
        perp_exit_days: int = 30,
    ) -> None:
        self.symbol = symbol
        self.top_n, self.lookback, self.btc_days = top_n, lookback_days, btc_days
        self.margin, self.coin_days, self.vol_days = margin_pct / 100, coin_days, vol_days
        self.target = target_vol_pct / 100
        self.min_volume, self.min_history = min_volume_m * 1e6, min_history
        self.perp_exit = perp_exit_days
        self.name = (
            f"Altcoin rotation, top {top_n} by {lookback_days}d while BTC > {btc_days}d "
            f"+{margin_pct}%, exit rules"
        )

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        missing = [c for c in (DELISTING, PERP_DAYS) if c not in panel.columns]
        if missing:
            raise ValueError(f"{self.name} needs {', '.join(missing)} on its panel")
        if panel.filter(pl.col("symbol") == BTC).is_empty():
            raise ValueError(f"{self.name} needs {BTC} on its panel (the regime)")
        require_daily(panel, self.name)
        close = pl.col("close")
        # A coin to avoid now: its delisting is announced, or its first perp is recent.
        flagged = pl.col(DELISTING) | (
            pl.col(PERP_DAYS).is_not_null() & (pl.col(PERP_DAYS) <= self.perp_exit)
        )
        scored = panel.sort("open_time").with_columns(
            _r=(close / close.shift(self.lookback)).over(BY) - 1,
            _trend=(close > close.shift(self.coin_days)).over(BY),
            _age=pl.int_range(1, pl.len() + 1).over(BY),
            _volume=pl.col("quote_volume").rolling_median(30, min_samples=30).over(BY),
            _vol=close.log().diff().rolling_std(self.vol_days, min_samples=self.vol_days).over(BY)
            * math.sqrt(365),
            _flagged=flagged.fill_null(False),
        )
        btc = (
            scored.filter(pl.col("symbol") == BTC)
            .with_columns(_ma=close.rolling_mean(self.btc_days, min_samples=self.btc_days))
            .select("open_time", _on=close > pl.col("_ma") * (1 + self.margin))
        )
        sundays = scored.filter(pl.col("open_time").dt.weekday() == 7).join(
            btc, on="open_time", how="left"
        )
        chosen = (
            sundays.filter(
                pl.col("_on").fill_null(False)
                & ~pl.col("symbol").is_in(NEVER + SPENT)
                & (pl.col("_age") >= self.min_history)
                & (pl.col("_volume") >= self.min_volume)
                & pl.col("_trend").fill_null(False)
                & pl.col("_r").is_not_null()
                & pl.col("_vol").is_not_null()
                & (pl.col("_vol") > 0)
                & ~pl.col("_flagged")
            )
            .sort(["open_time", "_r", "symbol"], descending=[False, True, False])
            .group_by("open_time", maintain_order=True)
            .head(self.top_n)
            .with_columns(
                weight=pl.min_horizontal(pl.lit(1.0), pl.lit(self.target) / pl.col("_vol"))
                / self.top_n
            )
        )
        weekly = chosen.select(list(DECISIONS))
        days = sundays.select("open_time").unique()
        idle = days.join(weekly, on="open_time", how="anti").select(
            "open_time", symbol=pl.lit(None, pl.Utf8), weight=pl.lit(0.0)
        )
        exits = self._exits(scored, chosen)
        out = pl.concat([weekly, idle, exits]).select(
            [pl.col(c).cast(t) for c, t in DECISIONS.items()]
        )
        return out.sort("open_time", "symbol", nulls_last=True) if out.height else no_decisions()

    def _exits(self, scored: pl.DataFrame, chosen: pl.DataFrame) -> pl.DataFrame:
        """A decision on each day (not a Sunday) when one of the week's picks becomes
        flagged: that pick to 0, the others kept as held (weight null)."""
        picks: dict[object, list[str]] = {}
        for t, s in chosen.select("open_time", "symbol").iter_rows():
            picks.setdefault(t, []).append(s)
        flags = {
            (s, t): f for s, t, f in scored.select("symbol", "open_time", "_flagged").iter_rows()
        }
        days = scored["open_time"].unique().sort().to_list()
        rows: list[tuple[object, str, float | None]] = []
        current: list[str] = []
        sold: set[str] = set()
        for t in days:
            if t.isoweekday() == 7:
                current, sold = picks.get(t, []), set()
                continue
            newly = [s for s in current if s not in sold and flags.get((s, t), False)]
            if newly:
                sold |= set(newly)
                rows += [(t, s, 0.0 if s in sold else None) for s in current]
        return pl.DataFrame(
            rows, schema={"open_time": pl.Datetime("us", "UTC"), "symbol": pl.Utf8,
                          "weight": pl.Float64}, orient="row",
        )  # fmt: skip
