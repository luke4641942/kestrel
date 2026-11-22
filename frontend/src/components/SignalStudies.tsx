import { useState } from "react";
import { api, type SignalPart, type SignalStudy } from "../api";
import { dateTime } from "../format";
import { useResource } from "../hooks";
import { sideText, signalVerdict, weekPct } from "../signalStudies";
import { Panel, Section, Stat, Status } from "./ui";

/** The weekly signal studies: do the weeks after a signal beat the rest? Not engine runs, so
 * they sit apart; nothing is open until a study is clicked. */
export function SignalStudies() {
  const studies = useResource(api.signalStudies, []);
  const list = studies.data ?? [];
  const [chosen, setChosen] = useState<string>();
  const current = list.find((s) => s.signal === chosen);
  if (studies.error || list.length === 0) return null;
  return (
    <Section
      title="Signal studies"
      description="Do the weeks after a signal beat the weeks without it? Sunday closes, BTC and ETH pooled, each run from its frozen note with kestrel signal-study. A pass leads to a strategy note, never straight to a trade. Select a study to see its results."
    >
      <Panel>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Study</th>
                <th>Verdict</th>
                <th className="num">Spread a week</th>
                <th className="num">P(spread &gt; 0)</th>
                <th>Measured</th>
              </tr>
            </thead>
            <tbody>
              {list.map((s) => (
                <tr
                  key={s.signal}
                  className={`clickable${s.signal === chosen ? " selected" : ""}`}
                  onClick={() => setChosen(s.signal === chosen ? undefined : s.signal)}
                  aria-selected={s.signal === chosen}
                >
                  <td>
                    <div style={{ fontWeight: 500 }}>{s.title}</div>
                    <div className="mono small tertiary">{s.note}</div>
                  </td>
                  <td className="nw">
                    <Status tone={signalVerdict(s).tone}>{signalVerdict(s).label}</Status>
                  </td>
                  <td className="num">{weekPct(s.weeks.spread)}</td>
                  <td className="num">{s.weeks.p_positive === null ? "n/a" : `${Math.round(s.weeks.p_positive * 100)}%`}</td>
                  <td className="nw secondary">{dateTime(s.measured_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {current && <SignalView study={current} />}
    </Section>
  );
}

function Part({ part }: { part: SignalPart }) {
  const w = part.weeks;
  return (
    <>
      <div className="stats">
        <Stat label="Weeks after it was on" value={weekPct(w.mean_on)} note={`${w.n_on} weeks`} />
        <Stat label="Weeks after it was off" value={weekPct(w.mean_off)} note={`${w.n_off} weeks`} />
        <Stat label="Spread" value={weekPct(w.spread)} note={`Costs to beat: ${weekPct(part.hurdle)}`} />
        <Stat label="Confidence it's above zero" value={w.p_positive === null ? "n/a" : `${Math.round(w.p_positive * 100)}%`} note="90% needed" />
      </div>
      {part.after_rise && part.after_fall && (
        <ul className="safety-events">
          <li>
            <Status tone={part.control_holds ? "ok" : "bad"}>{part.control_holds ? "pass" : "fail"}</Status> The control: positive after a rising week and after a falling one
          </li>
          <li className="secondary">After a rising week: {sideText(part.after_rise)}</li>
          <li className="secondary">After a falling week: {sideText(part.after_fall)}</li>
        </ul>
      )}
    </>
  );
}

function SignalView({ study: s }: { study: SignalStudy }) {
  const v = signalVerdict(s);
  return (
    <div className="run-stack">
      <Panel pad>
        <div className={`verdict verdict-${v.tone}`}>
          <h3>{s.title}</h3>
          <p className="secondary">{v.text}</p>
        </div>
        <Part part={s} />
      </Panel>
      {s.beside && (
        <Panel pad>
          <div className="verdict verdict-neutral">
            <h3>Beside it, deciding nothing</h3>
            <p className="secondary">
              Reported alongside by its note before the run, so it can't change the verdict above (it would be a setting chosen after
              seeing the results). Its own reading: {signalVerdict(s.beside).label.toLowerCase()}.
            </p>
          </div>
          <Part part={s.beside} />
        </Panel>
      )}
      <Panel pad>
        <pre className="mono small report">{s.report}</pre>
        <p className="small tertiary">
          Code {s.commit ?? "unknown"}. Pre-registered in research/strategies/{s.note}/.
        </p>
      </Panel>
    </div>
  );
}
