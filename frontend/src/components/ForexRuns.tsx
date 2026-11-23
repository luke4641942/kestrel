import { api } from "../api";
import { useResource } from "../hooks";
import { fxRunRow, poolLine } from "../markets";
import { Empty, Panel, Section } from "./ui";

/** Saved forex runs (kestrel fx-backtest), each deflated against the forex pool. Read-only:
 * forex runs start from the CLI, through a frozen note. */
export function ForexRuns() {
  const runs = useResource(api.fxRuns, [], undefined, true);
  const validation = useResource(api.validation, []);
  const list = runs.data ?? [];
  return (
    <Section
      title="Forex runs"
      description={`Run from the CLI (kestrel fx-backtest) through a frozen note, across the fixed pairs, before the 2023 forex holdout. Judged against holding nothing, after spreads and nightly financing; ${poolLine(validation.data, "forex")}.`}
    >
      <Panel>
        {list.length === 0 ? (
          <Empty title={runs.loading ? "Loading forex runs" : "No forex runs yet"}>
            {runs.error ? <p>{runs.error}</p> : !runs.loading && <p>A forex note runs once it is frozen.</p>}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Period</th>
                  <th className="num">Return</th>
                  <th className="num">Sharpe</th>
                  <th className="num">Deflated</th>
                  <th className="num">Max drawdown</th>
                  <th className="num">Gross</th>
                  <th className="num">Financing</th>
                  <th className="num">2-year windows</th>
                </tr>
              </thead>
              <tbody>
                {list.map((run) => {
                  const r = fxRunRow(run);
                  return (
                    <tr key={run.id}>
                      <td>
                        {run.strategy}
                        <div className="small secondary">
                          {run.research ? `${run.research.slug} v${run.research.version}` : "no note"} · {r.pairs}
                        </div>
                      </td>
                      <td className="nw">{r.period}</td>
                      <td className="num">{r.ret}</td>
                      <td className="num">{r.sharpe}</td>
                      <td className="num">{r.deflated}</td>
                      <td className="num">{r.drawdown}</td>
                      <td className="num">{r.gross}</td>
                      <td className="num nw">{r.financing}</td>
                      <td className="num">{r.windows}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </Section>
  );
}
