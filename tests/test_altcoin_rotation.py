"""The altcoin portfolio: who is eligible (history, volume, trend, never the spent coins),
the BTC regime, the momentum pick, and the two exit rules (a delisting announcement, a new
perp), plus the coin-event columns it reads and no lookahead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from kestrel.backtest.portfolio import validated_decisions
from kestrel.data import coin_events
from kestrel.strategies.altcoin_rotation import AltcoinRegimeRotation
from tests.backtest.test_portfolio import panel
from tests.test_btc_rotation import MONDAY, walk

SUNDAY = MONDAY + timedelta(days=6)


def market(extra: dict[str, list[float | None]] | None = None) -> pl.DataFrame:
    prices = {
        # Quiet walks, so each coin's own 30-day trend stays up and the ranking is clear.
        "BTCUSDT": walk(0.004, 1, vol=0.004),  # rising: the regime is on
        "ETHUSDT": walk(0.008, 2, vol=0.004),  # the strongest, never picked (holdout spent)
        "AAAUSDT": walk(0.006, 3, vol=0.004),
        "BBBUSDT": walk(0.004, 4, vol=0.004),
        "CCCUSDT": walk(0.003, 5, vol=0.004),
        "DDDUSDT": walk(0.002, 6, vol=0.004),
        **(extra or {}),
    }
    p = panel(prices, start=MONDAY, volume=5e7)
    return p.with_columns(
        pl.lit(False).alias(coin_events.DELISTING),
        pl.lit(None, pl.Int64).alias(coin_events.PERP_DAYS),
    )


def picks(out: pl.DataFrame, day: datetime) -> dict[str, float]:
    rows = out.filter((pl.col("open_time") == day) & pl.col("symbol").is_not_null())
    return {s: w for s, w in rows.select("symbol", "weight").iter_rows()}


def sundays(p: pl.DataFrame) -> list[datetime]:
    return sorted(p.filter(pl.col("open_time").dt.weekday() == 7)["open_time"].unique().to_list())


def test_it_holds_the_strongest_eligible_altcoins_after_a_year_while_btc_rises() -> None:
    p = market()
    out = AltcoinRegimeRotation().target_weights(p)
    late = sundays(p)[-1]
    chosen = picks(out, late)
    assert set(chosen) == {"AAAUSDT", "BBBUSDT", "CCCUSDT"}  # ETH never, DDD the weakest
    assert all(0 < w <= 1 / 3 for w in chosen.values())
    first_year = [d for d in sundays(p) if d < MONDAY + timedelta(days=364)]
    assert not any(picks(out, d) for d in first_year)  # a year of history first


def test_thin_coins_and_a_falling_btc_hold_nothing() -> None:
    thin = market().with_columns(
        pl.when(pl.col("symbol") == "AAAUSDT").then(1e6).otherwise(pl.col("quote_volume"))
        .alias("quote_volume")
    )  # fmt: skip
    assert "AAAUSDT" not in picks(AltcoinRegimeRotation().target_weights(thin), sundays(thin)[-1])
    falling = market({"BTCUSDT": walk(-0.004, 1, vol=0.004)})
    out = AltcoinRegimeRotation().target_weights(falling)
    assert not any(picks(out, d) for d in sundays(falling))


def test_a_delisting_announcement_sells_a_pick_the_next_day_and_keeps_the_others() -> None:
    p = market()
    last = sundays(p)[-2]
    wednesday = last + timedelta(days=3)
    flagged = p.with_columns(
        ((pl.col("symbol") == "AAAUSDT") & (pl.col("open_time") >= wednesday)).alias(
            coin_events.DELISTING
        )
    )
    out = AltcoinRegimeRotation().target_weights(flagged)
    exit_day = picks_all(out, wednesday)
    assert exit_day["AAAUSDT"] == 0.0
    assert exit_day["BBBUSDT"] is None
    assert exit_day["CCCUSDT"] is None
    assert "AAAUSDT" not in picks(out, sundays(p)[-1])  # and never bought again
    validated_decisions(AltcoinRegimeRotation(), flagged)  # the engine accepts them


def picks_all(out: pl.DataFrame, day: datetime) -> dict[str, float | None]:
    rows = out.filter(pl.col("open_time") == day)
    return {s: w for s, w in rows.select("symbol", "weight").iter_rows()}


def test_a_new_perp_keeps_a_coin_out_for_its_first_month() -> None:
    p = market()
    last = sundays(p)[-1]
    young = p.with_columns(
        pl.when(pl.col("symbol") == "AAAUSDT").then(10).otherwise(None).cast(pl.Int64)
        .alias(coin_events.PERP_DAYS)
    )  # fmt: skip
    assert "AAAUSDT" not in picks(AltcoinRegimeRotation().target_weights(young), last)
    old = p.with_columns(pl.lit(45, pl.Int64).alias(coin_events.PERP_DAYS))
    assert "AAAUSDT" in picks(AltcoinRegimeRotation().target_weights(old), last)


def test_it_needs_btc_and_the_event_columns() -> None:
    p = market()
    with pytest.raises(ValueError, match="BTCUSDT"):
        AltcoinRegimeRotation().target_weights(p.filter(pl.col("symbol") != "BTCUSDT"))
    with pytest.raises(ValueError, match="delisting_announced"):
        AltcoinRegimeRotation().target_weights(p.drop(coin_events.DELISTING))


def test_no_lookahead_a_decision_is_the_same_on_a_truncated_history() -> None:
    p = market()
    cut = sundays(p)[-3]
    full = AltcoinRegimeRotation().target_weights(p)
    part = AltcoinRegimeRotation().target_weights(p.filter(pl.col("open_time") <= cut))
    assert picks(full, cut) == picks(part, cut)


def test_coin_events_are_known_by_the_close_and_old_perps_never_count_as_new() -> None:
    p = panel({"AAAUSDT": [1.0] * 10, "BBBUSDT": [1.0] * 10, "CCCUSDT": [1.0] * 10},
              start=datetime(2022, 3, 1, tzinfo=UTC))  # fmt: skip
    announced = pl.DataFrame(
        {"coin": ["AAA"], "announced_at": [datetime(2022, 3, 3, 12, tzinfo=UTC)]}
    )
    perps = {
        "AAAUSDT": datetime(2022, 3, 5, 8, tzinfo=UTC),
        "BBBUSDT": datetime(2020, 1, 1, tzinfo=UTC),  # the archive's first day: listed before
        "CCCUSDT": None,
    }
    out = coin_events.attach(p, announced, perps).sort("symbol", "open_time")
    aaa = out.filter(pl.col("symbol") == "AAAUSDT")
    assert aaa[coin_events.DELISTING].to_list()[:4] == [False, False, True, True]  # 3 Mar's close
    assert aaa[coin_events.PERP_DAYS].to_list()[:6] == [None, None, None, None, 0, 1]
    bbb = out.filter(pl.col("symbol") == "BBBUSDT")
    assert set(bbb[coin_events.PERP_DAYS].to_list()) == {coin_events.CENSORED_DAYS}
    assert out.filter(pl.col("symbol") == "CCCUSDT")[coin_events.PERP_DAYS].null_count() == 10
