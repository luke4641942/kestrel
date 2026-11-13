"""Nearness to the 52-week high across large coins (research).

Investors anchor on a coin's 52-week high: as the price approaches it, they treat it as
a ceiling and sell, so good news is priced in slowly and coins near their high keep
rising. Jia, Simkins, Yan, Zhang and Zhao (Journal of Banking & Finance, 2026) report
that cryptocurrencies closer to their 52-week high earn more the following week, beyond
ordinary momentum. Their result is a long-short spread; Kestrel can only hold the long
side.

Every Sunday's daily close (UTC) is a decision. From the weekly large-coin basket
(kestrel.strategies.basket: by default the 20 coins with the highest 30-day median volume
and at least 365 days of unbroken history, so every coin has a full year to take its high
from), each coin's nearness is its close divided by the highest high of its last 365
daily bars, today's included. The ``hold_n`` nearest are held at ``1 / hold_n`` each.
Always invested once the basket exists: the signal ranks coins, it doesn't time the
market. Its comparison is the same basket held equally (``large-basket`` with top_n 20
and min_history_days 365); beating BTC alone would only show that large coins rose.
"""

from __future__ import annotations

import polars as pl

from kestrel.strategies.basket import as_decisions, no_decisions, require_daily, weekly_basket

YEAR_DAYS = 365


class HighAnchor:
    def __init__(
        self,
        symbol: str = "UNIVERSE",
        hold_n: int = 3,
        basket_n: int = 20,
        min_volume_musd: int = 20,
    ) -> None:
        if not hold_n <= basket_n:
            raise ValueError("hold_n must be at most basket_n")
        self.symbol = symbol
        self.hold_n, self.basket_n, self.min_volume = hold_n, basket_n, min_volume_musd
        self.name = f"nearest {hold_n} to their 52-week high, of the top {basket_n} coins"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        if panel.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        nearness = pl.col("close") / pl.col("high").rolling_max(YEAR_DAYS)
        basket = weekly_basket(
            panel, self.basket_n, YEAR_DAYS, self.min_volume, {"_near": nearness}
        )
        chosen = (
            basket.filter(pl.col("_near").is_not_null())
            .sort(["open_time", "_near", "symbol"], descending=[False, True, False])
            .with_columns(_rank=pl.int_range(pl.len()).over("open_time"))
            .filter(pl.col("_rank") < self.hold_n)
        )
        return as_decisions(panel, chosen.with_columns(weight=pl.lit(1 / self.hold_n)))
