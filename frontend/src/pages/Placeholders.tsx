import { Card, Empty, PageHeader, Pill } from "../components/ui";

function Planned({ items }: { items: { title: string; body: string }[] }) {
  return (
    <div className="planned">
      {items.map((i) => (
        <div key={i.title} className="card">
          <h4>{i.title}</h4>
          <p>{i.body}</p>
        </div>
      ))}
    </div>
  );
}

export function Backtests() {
  return (
    <>
      <PageHeader title="Backtests" subtitle="Run, inspect and compare strategy backtests." actions={<Pill>Phase 2</Pill>} />
      <Card>
        <Empty title="The backtester is the next build">
          <p>
            Backtests will run as jobs from here, exactly like ingestion does now, and every run is kept so results can be
            compared side by side.
          </p>
          <Planned
            items={[
              { title: "Equity & drawdown", body: "Net equity curve against buy-and-hold, with underwater drawdown." },
              { title: "Cost attribution", body: "Gross vs net PnL, so you can see how much fees and slippage ate." },
              { title: "Trade blotter", body: "Every simulated fill with price, size, fee and reason for entry or exit." },
              { title: "Metrics", body: "Sharpe, Sortino, max drawdown, turnover, exposure and hit rate." },
              { title: "Run comparison", body: "Overlay runs and diff their parameters." },
              { title: "Trial log", body: "Every variant ever tried, so the Sharpe can be deflated honestly (P3)." },
            ]}
          />
        </Empty>
      </Card>
    </>
  );
}

export function Live() {
  return (
    <>
      <PageHeader title="Trading" subtitle="Paper and live strategy control." actions={<Pill>Phase 4</Pill>} />
      <Card>
        <Empty title="No strategies deployed">
          <p>
            Execution comes after the backtester and validation are proven. Strategies will run through the same code path
            here as in backtests, against Binance Spot Testnet first.
          </p>
          <div className="row" style={{ justifyContent: "center" }}>
            <button className="btn btn-danger" disabled title="Available once execution is built">
              Kill switch: flatten all
            </button>
          </div>
          <Planned
            items={[
              { title: "Positions & orders", body: "Live state, reconciled against the exchange on every cycle." },
              { title: "Live vs backtest", body: "Real fills against what the backtest expected; drift is flagged." },
              { title: "Risk limits", body: "Max position, daily loss stop and drawdown halt, set before go-live." },
            ]}
          />
        </Empty>
      </Card>
    </>
  );
}
