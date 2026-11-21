import { useState } from "react";
import { api, type EventStudy } from "../api";
import { eventName, eventVerdict, pathGeometry } from "../eventStudies";
import { dateTime } from "../format";
import { useResource } from "../hooks";
import { Empty, Panel, Section, Stat, Status } from "./ui";

const pct = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}%`;

/** The event studies: what coins did around an event, against the other coins. Not engine
 * runs, so they sit apart; nothing is open until a study is clicked. */
export function EventStudies() {
  const studies = useResource(api.eventStudies, []);
  const list = studies.data ?? [];
  const [chosen, setChosen] = useState<string>();
  const current = list.find((s) => s.name === chosen);
  if (studies.error || list.length === 0) return null;
  return (
    <Section
      title="Event studies"
      description="What coins did around an event (a perp listing, an unlock), against the other coins at the same time, each run once from its frozen note with kestrel event-study. A pass is an exit rule: a reason not to hold a coin, not a trade. Select a study to see its results."
    >
      <Panel>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Study</th>
                <th>Verdict</th>
                <th className="num">Events</th>
                <th className="num">Against the other coins</th>
                <th>Measured</th>
              </tr>
            </thead>
            <tbody>
              {list.map((s) => (
                <tr
                  key={s.name}
                  className={`clickable${s.name === chosen ? " selected" : ""}`}
                  onClick={() => setChosen(s.name === chosen ? undefined : s.name)}
                  aria-selected={s.name === chosen}
                >
                  <td>
                    <div style={{ fontWeight: 500 }}>{eventName(s)}</div>
                    <div className="mono small tertiary">{s.note}</div>
                  </td>
                  <td className="nw">
                    <Status tone={eventVerdict(s).tone}>{eventVerdict(s).label}</Status>
                  </td>
                  <td className="num">{s.events}</td>
                  <td className="num">{pct(s.mean)}</td>
                  <td className="nw secondary">{dateTime(s.measured_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {current && <EventView study={current} />}
    </Section>
  );
}

function EventView({ study: s }: { study: EventStudy }) {
  const v = eventVerdict(s);
  return (
    <div className="run-stack">
      <Panel pad>
        <div className={`verdict verdict-${v.tone}`}>
          <h3>{eventName(s)}</h3>
          <p className="secondary">{v.text}</p>
        </div>
        <div className="stats">
          <Stat label="Against the other coins" value={pct(s.mean)} note={`Mean over ${s.event_days} event days`} />
          <Stat label="Confidence it's below zero" value={`${Math.round(s.p_below * 100)}%`} note={`${Math.round(s.share_negative * 100)}% of events below zero`} />
          <Stat
            label="Same coins, random days"
            value={s.placebo_median === null ? "n/a" : pct(s.placebo_median)}
            note={`${Math.round(s.placebo_share_at_or_below * 100)}% of random draws as bad`}
          />
          <Stat label="Events" value={s.events} />
        </div>
        <ul className="safety-events">
          {Object.entries(s.tests).map(([name, ok]) => (
            <li key={name}>
              <Status tone={ok ? "ok" : "bad"}>{ok ? "pass" : "fail"}</Status> {name}
            </li>
          ))}
        </ul>
      </Panel>
      <PathChart study={s} />
      <Panel pad>
        <pre className="mono small report">{s.report}</pre>
        <p className="small tertiary">
          Code {s.commit ?? "unknown"}. Pre-registered in research/strategies/{s.note}/.
        </p>
      </Panel>
    </div>
  );
}

const W = 640;
const H = 220;

function PathChart({ study: s }: { study: EventStudy }) {
  const geo = pathGeometry(s.path, W, H);
  if (!geo) {
    return (
      <Panel>
        <Empty title="No path saved for this study" />
      </Panel>
    );
  }
  return (
    <Panel pad>
      <div className="chart-top">
        <span className="small secondary">
          Average return against the other coins from 30 days before the event (day 0) to 30 days after
        </span>
      </div>
      <svg viewBox={`-44 -8 ${W + 52} ${H + 30}`} className="event-path" role="img" aria-label="Average path around the event">
        {geo.ticks.map((t) => (
          <g key={t.label}>
            <line x1={0} x2={W} y1={t.y} y2={t.y} className="event-grid" />
            <text x={-8} y={t.y + 4} className="event-tick" textAnchor="end">
              {t.label}
            </text>
          </g>
        ))}
        <line x1={0} x2={W} y1={geo.zeroY} y2={geo.zeroY} className="event-zero" />
        <line x1={geo.day0X} x2={geo.day0X} y1={0} y2={H} className="event-day0" />
        <text x={geo.day0X} y={H + 18} className="event-tick" textAnchor="middle">
          day 0
        </text>
        <text x={0} y={H + 18} className="event-tick" textAnchor="start">
          −30
        </text>
        <text x={W} y={H + 18} className="event-tick" textAnchor="end">
          +30
        </text>
        <polyline points={geo.points} className="event-line" />
      </svg>
    </Panel>
  );
}
