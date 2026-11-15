"""Cost model tests. Worked examples are computed by hand in the comments so every
number can be checked without running code."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side
from kestrel.config import CostConfig, InstrumentRules, load_config

D = Decimal

# Round numbers so the worked examples are easy to follow:
# fee 10 bps, half-spread 1 bp, impact coef 10 bps.
COSTS = CostConfig(
    taker_fee_bps=D(10), half_spread_bps=D(1), impact_coef_bps=D(10), max_participation=D("0.01")
)
BTC = InstrumentRules("BTCUSDT", step_size=D("0.00001"), min_qty=D("0.00001"), min_notional=D(5))


@pytest.fixture
def model() -> CostModel:
    return CostModel(COSTS)


def fill(model: CostModel, side: Side, qty: str, volume: str = "738000") -> Fill:
    result = model.fill_market(side, D(qty), D(60_000), D(volume), BTC)
    assert isinstance(result, Fill), result
    return result


def test_worked_buy(model: CostModel) -> None:
    # Requested 0.00123456 BTC rounds down to 0.00123. Notional at reference = 73.8.
    # Participation = 73.8 / 738,000 = 0.0001; sqrt = 0.01; impact = 10 bps * 0.01 = 0.1 bp.
    # Slip = 1 bp + 0.1 bp = 0.00011, so price = 60,000 * 1.00011 = 60,006.6.
    f = fill(model, Side.BUY, "0.00123456")
    assert f.quantity == D("0.00123")
    assert f.price == D("60006.6")
    assert f.fee == D("0.073808118")  # 0.00123 * 60,006.6 * 0.001
    assert f.spread_cost == D("0.00738")  # 73.8 * 0.0001
    assert f.impact_cost == D("0.000738")  # 73.8 * 0.00001
    assert f.total_cost == D("0.081926118")
    assert f.cash_flow == D("-73.881926118")  # 73.8 paid + every cost


def test_worked_sell(model: CostModel) -> None:
    # Same order sold: price = 60,000 * 0.99989 = 59,993.4; fee on proceeds 73.791882.
    f = fill(model, Side.SELL, "0.00123")
    assert f.price == D("59993.4")
    assert f.fee == D("0.073791882")
    assert f.total_cost == D("0.081909882")
    assert f.cash_flow == D("73.718090118")  # 73.8 at reference minus every cost


# Decimal works to 28 significant digits, so an irrational sqrt can round differently
# in the last digit between two algebraically equal paths. 1e-20 is far below a cent.
TINY = D("1e-20")


@pytest.mark.parametrize("side", list(Side))
def test_cost_rate_matches_fill(model: CostModel, side: Side) -> None:
    """The vectorised engine's cost rate and the event engine's fill must agree."""
    f = fill(model, side, "0.0042", volume="2500000")
    notional = f.quantity * f.reference_price
    assert abs(model.cost_rate(side, notional, D(2_500_000)) * notional - f.total_cost) < TINY


def test_quantity_rounds_down_never_up(model: CostModel) -> None:
    assert fill(model, Side.BUY, "0.0001999999").quantity == D("0.00019")


def test_impact_scales_with_square_root_of_size(model: CostModel) -> None:
    small = fill(model, Side.BUY, "0.001", volume="6000000")
    large = fill(model, Side.BUY, "0.004", volume="6000000")
    impact_rate = lambda f: f.impact_cost / (f.quantity * f.reference_price)  # noqa: E731
    assert abs(impact_rate(large) - 2 * impact_rate(small)) < TINY


@pytest.mark.parametrize(
    ("qty", "volume", "reason"),
    [
        ("0.000009", "738000", "below the minimum 0.00001"),  # rounds to 0
        ("0.00008", "738000", "notional 4.80 is below the minimum 5"),
        ("0.001", "0", "no traded volume"),
        ("0.1", "500000", "1.20% of the last bar's volume (limit 1.00%)"),
        ("-0.001", "738000", "must be positive"),
    ],
)
def test_rejections_explain_why(model: CostModel, qty: str, volume: str, reason: str) -> None:
    result = model.fill_market(Side.BUY, D(qty), D(60_000), D(volume), BTC)
    assert isinstance(result, Rejection)
    assert reason in result.reason
    assert result.requested_quantity == D(qty)


def test_cost_rate_rejects_zero_volume(model: CostModel) -> None:
    with pytest.raises(ValueError, match="positive"):
        model.cost_rate(Side.BUY, D(100), D(0))


def test_round_trip_at_repo_defaults_is_about_22_bps() -> None:
    """Headline number for the README: what a liquid BTC round trip costs as configured.

    A A$500-sized order (~300 USDT) against a typical 4h BTCUSDT bar (~US$500m of
    volume) has negligible impact, so the round trip is ~2 x (7.5 bps fee + 1 bp spread).
    """
    config = load_config(Path(__file__).resolve().parents[2] / "kestrel.toml")
    model = CostModel(config.costs)
    rules = config.instrument("BTCUSDT")
    buy = model.fill_market(Side.BUY, D("0.005"), D(60_000), D(500_000_000), rules)
    sell = model.fill_market(Side.SELL, D("0.005"), D(60_000), D(500_000_000), rules)
    assert isinstance(buy, Fill)
    assert isinstance(sell, Fill)
    round_trip = (buy.total_cost + sell.total_cost) / (buy.quantity * buy.reference_price)
    assert D("0.0016") < round_trip < D("0.0018")
