import { useEffect, useState } from "react";
import { api, type Dataset, type IndicatorInfo, type Interval } from "../api";
import { CandleChart } from "../components/CandleChart";
import { IndicatorPicker, useIndicatorSelection } from "../components/IndicatorPicker";
import { Empty, PageHeader, Panel, Section } from "../components/ui";
import {
  RANGES,
  barsFor,
  intervalsFor,
  loadChoice,
  marketOptions,
  saveChoice,
  switchMarket,
  type ChartChoice,
} from "../charts";
import { useResource } from "../hooks";
import { indicatorColor } from "../indicators";

// A place to look at any stored market with Kestrel's indicators, each explained. The
// indicators are fixed at textbook settings (kestrel.indicators.PRESETS): this page is
// for looking, not tuning. Bars and indicator values come from the API.
export function Charts() {
  const datasets = useResource(api.datasets, []);
  const list = datasets.data ?? [];
  const [choice, setChoice] = useState<ChartChoice>();
  useEffect(() => {
    if (!choice && list.length) setChoice(loadChoice(list));
  }, [choice, list]);
  const update = (next: ChartChoice) => {
    setChoice(next);
    saveChoice(next);
  };

  return (
    <>
      <PageHeader title="Charts" subtitle="Any stored market, with Kestrel's indicators and what each one means." />
      {datasets.error ? (
        <Panel>
          <Empty title="Couldn't load the markets">
            <p>{datasets.error}</p>
          </Empty>
        </Panel>
      ) : !list.length ? (
        <Panel>
          <Empty title={datasets.loading ? "Loading markets" : "No data yet"}>
            {!datasets.loading && <p>Ingest a market on the Data page first.</p>}
          </Empty>
        </Panel>
      ) : (
        choice && <ChartView datasets={list} choice={choice} onChange={update} />
      )}
    </>
  );
}

function ChartView({
  datasets,
  choice,
  onChange,
}: {
  datasets: Dataset[];
  choice: ChartChoice;
  onChange: (c: ChartChoice) => void;
}) {
  const ind = useIndicatorSelection("kestrel.indicators.charts");
  const limit = barsFor(choice.interval, choice.days);
  const bars = useResource(
    () => api.bars(choice.symbol, choice.interval, limit, ind.active),
    [choice.symbol, choice.interval, limit, ind.active.join()],
  );
  const b = bars.data && bars.data.symbol === choice.symbol && bars.data.interval === choice.interval ? bars.data : undefined;
  const { main, rest } = marketOptions(datasets);
  const intervals = intervalsFor(datasets, choice.symbol);
  const shown = ind.menu.filter((m) => ind.active.includes(m.key));

  return (
    <div className="run-stack">
      <Section>
        <div className="form form-charts">
          <div className="field">
            <label htmlFor="ch-market">Market</label>
            <select
              id="ch-market"
              value={choice.symbol}
              onChange={(e) => onChange(switchMarket(datasets, choice, e.target.value))}
            >
              {main.length > 0 && (
                <optgroup label="Main markets">
                  {main.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </optgroup>
              )}
              {rest.length > 0 && (
                <optgroup label={`All coins (${rest.length.toLocaleString("en-AU")})`}>
                  {rest.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ch-interval">Bar size</label>
            <select
              id="ch-interval"
              value={choice.interval}
              onChange={(e) => onChange({ ...choice, interval: e.target.value as Interval })}
            >
              {intervals.map((i) => (
                <option key={i} value={i}>
                  {i}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ch-range">Range</label>
            <select
              id="ch-range"
              value={choice.days ?? "all"}
              onChange={(e) => onChange({ ...choice, days: e.target.value === "all" ? null : Number(e.target.value) })}
            >
              {RANGES.map((r) => (
                <option key={r.label} value={r.days ?? "all"}>
                  {r.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </Section>

      <Panel>
        <div className="chart-indicators">
          <IndicatorPicker menu={ind.menu} selected={ind.active} onChange={ind.change} />
        </div>
        {bars.error ? (
          <Empty title="Couldn't load the bars">
            <p>{bars.error}</p>
          </Empty>
        ) : b ? (
          <CandleChart bars={b} menu={ind.menu} indicators={ind.active} />
        ) : (
          <div className="chart" />
        )}
        {limit === 20_000 && b && b.time.length === limit && (
          <p className="small tertiary chart-cap">Showing the latest 20,000 bars, the most one chart loads.</p>
        )}
      </Panel>

      <Section
        title="What you're looking at"
        description="Every indicator uses its textbook settings and only the bars up to each point, so nothing on the chart knows the future. A strategy that uses one states its setting in a frozen research note."
      >
        {shown.length === 0 ? (
          <Panel>
            <Empty title="No indicators on">
              <p>Pick one above: on-price indicators draw over the candles, the rest in panes below.</p>
            </Empty>
          </Panel>
        ) : (
          <div className="indicator-cards">
            {shown.map((m) => (
              <IndicatorCard key={m.key} info={m} menu={ind.menu} />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}

function IndicatorCard({ info, menu }: { info: IndicatorInfo; menu: IndicatorInfo[] }) {
  return (
    <Panel pad>
      <h3 className="indicator-card-title">
        <i className="swatch" style={{ background: `var(${indicatorColor(menu, info.key)})` }} aria-hidden="true" />
        {info.label}
        <span className="small tertiary">{info.pane === "price" ? "on the price" : "in its own pane"}</span>
      </h3>
      <p className="indicator-reading">{info.reading}</p>
      {info.guides.length > 0 && (
        <p className="small secondary">
          Guide lines at {info.guides.map((g) => (info.percent ? `${g * 100}%` : `${g}`)).join(" and ")}.
        </p>
      )}
    </Panel>
  );
}
