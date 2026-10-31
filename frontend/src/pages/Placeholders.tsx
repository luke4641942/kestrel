import { PageHeader, Panel } from "../components/ui";

function Scope({ items }: { items: [string, string][] }) {
  return (
    <ul className="scope">
      {items.map(([name, body]) => (
        <li key={name}>
          <strong>{name}</strong>
          <span>{body}</span>
        </li>
      ))}
    </ul>
  );
}

export function Backtests() {
  return (
    <>
      <PageHeader title="Backtests" subtitle="Run, inspect and compare strategy backtests." />
      <Panel pad>
        <div className="empty" style={{ padding: 8 }}>
          <h3>Not built yet. This is phase 2.</h3>
          <p>
            Backtests will run as jobs, the same way ingests do, and every run is kept so results can be compared
            side by side.
          </p>
          <Scope
            items={[
              ["Equity and drawdown", "Net equity against buy-and-hold, with the drawdown underneath."],
              ["Cost attribution", "Gross and net PnL, showing what fees and slippage cost."],
              ["Trade blotter", "Every simulated fill with price, size, fee and the reason for it."],
              ["Metrics", "Sharpe, Sortino, max drawdown, turnover, exposure and hit rate."],
              ["Run comparison", "Overlay runs and compare their parameters."],
              ["Trial log", "Every variant tried, so results can be deflated for multiple testing."],
            ]}
          />
        </div>
      </Panel>
    </>
  );
}

export function Trading() {
  return (
    <>
      <PageHeader
        title="Trading"
        subtitle="Paper and live strategy control."
        actions={
          <button className="btn btn-danger" disabled title="Available once execution is built">
            Flatten all positions
          </button>
        }
      />
      <Panel pad>
        <div className="empty" style={{ padding: 8 }}>
          <h3>No strategies deployed</h3>
          <p>
            Execution comes after the backtester and validation are proven. Strategies will run through the same code
            path here as in backtests, on Binance Spot Testnet first.
          </p>
          <Scope
            items={[
              ["Positions and orders", "Live state, reconciled against the exchange every cycle."],
              ["Live against backtest", "Real fills compared with what the backtest expected, with drift flagged."],
              ["Risk limits", "Maximum position, daily loss stop and drawdown halt, set before going live."],
            ]}
          />
        </div>
      </Panel>
    </>
  );
}
