"""Interest rates for forex financing (kestrel.data.rates). Offline."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from kestrel.data import external, rates


def monthly(values: dict[date, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": list(values), "value": list(values.values())}, schema=external.SCHEMA
    )


def test_series_names() -> None:
    assert rates.series("usd") == "fred:IR3TIB01USM156N"
    assert rates.series("EUR") == "fred:IR3TIB01EZM156N"
    with pytest.raises(rates.RatesError, match="no rate series"):
        rates.series("XAU")


def test_a_months_rate_is_charged_and_a_blank_is_bridged_and_counted() -> None:
    r = rates.Rates({"USD": {date(2020, 3, 1): 1.35, date(2020, 5, 1): 0.17}})
    assert r(("USD"), datetime(2020, 3, 9, tzinfo=UTC)) == pytest.approx(0.0135)
    assert r("USD", datetime(2020, 4, 2, tzinfo=UTC)) == pytest.approx(0.0135)  # April blank
    assert r("USD", datetime(2020, 4, 3, tzinfo=UTC)) == pytest.approx(0.0135)
    assert r.carried == {("USD", date(2020, 4, 1)): 2}
    assert r.carried_days == 2
    assert r("USD", datetime(2020, 5, 1, tzinfo=UTC)) == pytest.approx(0.0017)
    with pytest.raises(rates.RatesError, match="no USD rate for 2020-08"):
        r("USD", datetime(2020, 8, 1, tzinfo=UTC))  # three months on: too long to bridge
    with pytest.raises(rates.RatesError, match="no rates loaded for JPY"):
        r("JPY", datetime(2020, 5, 1, tzinfo=UTC))


def test_a_decision_only_sees_the_month_before_last() -> None:
    r = rates.Rates({"AUD": {date(2024, 1, 1): 4.3, date(2024, 2, 1): 4.35, date(2024, 3, 1): 4.4}})
    assert r.known_rate("AUD", datetime(2024, 3, 31, tzinfo=UTC)) == pytest.approx(0.043)
    assert r.known_rate("AUD", datetime(2024, 4, 1, tzinfo=UTC)) == pytest.approx(0.0435)
    assert r.known_rate("AUD", datetime(2024, 2, 1, tzinfo=UTC)) is None


def test_ingesting_reports_blank_months_and_loads_back(tmp_path: Path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        name = request.url.params["id"]
        rows = (
            ["2005-01-01,3.0", "2005-02-01,.", "2005-03-01,3.1"]
            if "US" in name
            else ["2005-01-01,2.0", "2005-02-01,2.1", "2005-03-01,2.2"]
        )
        return httpx.Response(200, text=f"observation_date,{name}\n" + "\n".join(rows) + "\n")

    found = rates.ingest(
        tmp_path, httpx.Client(transport=httpx.MockTransport(handle)), date(2005, 1, 1)
    )
    by = {r.currency: r for r in found}
    assert set(by) == set(rates.COUNTRY)
    assert by["USD"].missing == [date(2005, 2, 1)]
    assert by["EUR"].missing == []
    loaded = rates.Rates.load(tmp_path, ["USD", "EUR"])
    assert loaded("EUR", datetime(2005, 2, 10, tzinfo=UTC)) == pytest.approx(0.021)
    assert loaded("USD", datetime(2005, 2, 10, tzinfo=UTC)) == pytest.approx(0.03)  # bridged


def test_rates_drive_the_engines_financing() -> None:
    from decimal import Decimal

    from kestrel.backtest.fx_engine import run_fx
    from kestrel.config import FxConfig
    from tests.backtest.test_fx_engine import T0, pair_frame, weights

    f = pair_frame([100.0] * 4)
    r = rates.Rates({"EUR": {date(2010, 1, 1): 5.0}, "AUD": {date(2010, 1, 1): 1.0}})
    cfg = FxConfig(datetime(2023, 1, 1, tzinfo=UTC), ("EURAUD",), "AUD", Decimal(3), Decimal(0))
    out = run_fx("t", {"EURAUD": f}, weights(f, EURAUD=[1.0] * 4), cfg, 0.5, r, 1000)
    assert T0.month == 1
    assert out.financing == pytest.approx(1000 * 0.04 / 365 * 3)  # 3 nights held
