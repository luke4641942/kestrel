import { useState } from "react";
import { api } from "../api";
import { dateTime } from "../format";
import { useResource } from "../hooks";
import { STUDY_CASH, curveMoney, shortVerdict, studyName } from "../shortStudies";
import { money } from "../summary";
import { EquityChart } from "./EquityChart";
import { Empty, Panel, Section, Stat, Status } from "./ui";

/** The hypothetical shorting studies: not engine runs (the engine is spot, long-only), so
 * they sit apart from the runs, each with its tests, its curve and its full report. */
export function ShortStudies() {
  const studies = useResource(api.shortStudies, []);
  const list = studies.data ?? [];
  const [chosen, setChosen] = useState<string>();
  // Nothing open until a row is clicked (and a second click closes it), so the runs below
  // stay near the top of the page.
  const current = list.find((s) => s.name === chosen);
  if (studies.error || list.length === 0) return null;
  const v = current ? shortVerdict(current) : null;
  const [main, versus] = current?.curves ?? [];
  const m = curveMoney(main);
  const alt = curveMoney(versus);
  const pct = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}%`;
  const year = (t: number) => new Date(t * 1000).toISOString().slice(0, 7);
  return (
    <Section
      title="Hypothetical shorting studies"
      description="Measured outside the engine, which can't short: each was run once from its frozen note with kestrel short-study, after costs. A pass is labelled needs-shorting and is never traded. Select a study to see its results."
    >
      <Panel>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Study</th>
                <th>Verdict</th>
                <th>Measured</th>
              </tr>
            </thead>
            <tbody>
              {list.map((s) => (
                <tr
                  key={s.name}
                  className={`clickable${s.name === current?.name ? " selected" : ""}`}
                  onClick={() => setChosen(s.name === chosen ? undefined : s.name)}
                  aria-selected={s.name === current?.name}
                >
                  <td>
                    <div style={{ fontWeight: 500 }}>{studyName(s)}</div>
                    <div className="mono small tertiary">{s.note}</div>
                  </td>
                  <td className="nw">
                    <Status tone={shortVerdict(s).tone}>{shortVerdict(s).label}</Status>
                  </td>
                  <td className="nw secondary">{dateTime(s.measured_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {current && v && (
        <div className="run-stack">
          <Panel pad>
            <div className={`verdict verdict-${v.tone}`}>
              <h3>{studyName(current)}</h3>
              <p className="secondary">{v.text}</p>
            </div>
            {m && (
              <div className="stats">
                <Stat
                  label="Net profit, after costs"
                  value={`${money(m.profit)} USDT`}
                  note={`On ${STUDY_CASH} USDT, ${year(m.first)} to ${year(m.last)}`}
                />
                <Stat label="Return" value={pct(m.total)} note={`${pct(m.perYear)} a year`} />
                <Stat label="Worst drawdown" value={pct(m.worst)} note="Price gap between spot and perp not modelled" />
                {alt && versus && (
                  <Stat label={versus.label} value={`${money(alt.profit)} USDT`} note={`${pct(alt.total)} over the same years`} />
                )}
              </div>
            )}
            <ul className="safety-events">
              {Object.entries(current.tests).map(([name, ok]) => (
                <li key={name}>
                  <Status tone={ok ? "ok" : "bad"}>{ok ? "pass" : "fail"}</Status> {name}
                </li>
              ))}
            </ul>
          </Panel>
          {main && main.series.time.length > 1 ? (
            <Panel pad>
              <div className="chart-top">
                <span className="small secondary">Growth of 1 (log scale), drawdown underneath</span>
                <span className="cost-key">
                  <span>
                    <i className="swatch" style={{ background: "var(--text)" }} />
                    {main.label}
                  </span>
                  {versus && (
                    <span>
                      <i className="swatch swatch-dashed" />
                      {versus.label}
                    </span>
                  )}
                </span>
              </div>
              <EquityChart equity={main.series} benchmark={(versus ?? main).series} />
            </Panel>
          ) : (
            <Panel>
              <Empty title="No curve saved for this study">
                <p>The full report is below.</p>
              </Empty>
            </Panel>
          )}
          <Panel pad>
            <pre className="mono small report">{current.report}</pre>
            <p className="small tertiary">
              Code {current.commit ?? "unknown"}. Pre-registered in research/strategies/{current.note}/.
            </p>
          </Panel>
        </div>
      )}
    </Section>
  );
}
