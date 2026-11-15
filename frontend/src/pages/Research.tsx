import { useMemo, useState } from "react";
import { api } from "../api";
import { LuckChart } from "../components/LuckChart";
import { Empty, PageHeader, Panel, Section, Stat, Status } from "../components/ui";
import { date } from "../format";
import { useResource } from "../hooks";
import { byFamily, luckSeries, tally } from "../researchView";
import { DECISION, luckBar } from "../summary";

// Everything here is read from the API (research/decisions.toml, the trial log and
// research/findings.md); the page only groups and draws it.
export function Research() {
  const decisions = useResource(api.decisions, []);
  const validation = useResource(api.validation, []);
  const history = useResource(api.validationHistory, []);
  const rules = useResource(api.rules, []);
  const [open, setOpen] = useState<string | null>(null);

  const rows = useMemo(() => byFamily(decisions.data ?? []), [decisions.data]);
  const points = useMemo(() => luckSeries(history.data ?? []), [history.data]);
  const all = decisions.data ?? [];
  const count = (k: "pursue" | "watch" | "drop") => all.filter((d) => d.decision === k).length;

  return (
    <>
      <PageHeader
        title="Research"
        subtitle="Everything tested, grouped by kind, with what was decided and what it cost in luck."
      />

      <Panel>
        <div className="stats stats-5">
          <Stat label="Strategy and market decisions" value={all.length || "–"} />
          <Stat label="Pursue" value={count("pursue")} note="to paper trading, not proven" />
          <Stat label="Watch or drop" value={`${count("watch")} · ${count("drop")}`} />
          <Stat label="Trials logged" value={validation.data ? validation.data.trials.toLocaleString("en-AU") : "–"} />
          <Stat
            label="Luck bar"
            value={validation.data ? validation.data.expected_max_sharpe.toFixed(2) : "–"}
            note="annual Sharpe"
          />
        </div>
      </Panel>

      <Section
        title="What has been tried, by family"
        description="Each strategy on each market gets one decision. Open a family for every decision and its reason."
      >
        <Panel>
          {rows.length === 0 ? (
            <Empty title={decisions.loading ? "Loading decisions" : "No decisions yet"}>
              {decisions.error && <p>{decisions.error}</p>}
            </Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Family</th>
                    <th className="num">Decisions</th>
                    <th>Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const isOpen = open === r.family.key;
                    return [
                      <tr key={r.family.key} className="clickable" onClick={() => setOpen(isOpen ? null : r.family.key)}>
                        <td>
                          <button
                            type="button"
                            className="link-button"
                            aria-expanded={isOpen}
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpen(isOpen ? null : r.family.key);
                            }}
                          >
                            {r.family.name}
                          </button>
                          <div className="small secondary">{r.family.what}</div>
                        </td>
                        <td className="num">{r.decisions.length}</td>
                        <td className="nw">{tally(r)}</td>
                      </tr>,
                      isOpen && (
                        <tr key={`${r.family.key}-detail`}>
                          <td colSpan={3}>
                            <ul className="family-decisions">
                              {r.decisions.map((d) => (
                                <li key={`${d.strategy}-${d.market}`}>
                                  <div>
                                    <Status tone={DECISION[d.decision].tone}>{DECISION[d.decision].label}</Status>{" "}
                                    <span style={{ fontWeight: 500 }}>{d.name}</span>{" "}
                                    <span className="secondary">
                                      {d.market} · {date(d.decided)}
                                    </span>
                                  </div>
                                  <p className="small secondary">{d.reason}</p>
                                </li>
                              ))}
                            </ul>
                          </td>
                        </tr>
                      ),
                    ];
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </Section>

      <Section
        title="The luck bar over time"
        description="The annual Sharpe the best of every trial so far would reach by luck alone, after each run or study. It rises with the number of trials and with how spread out their Sharpes are: very bad strategies widen the spread, so they raise the bar too."
      >
        <Panel pad>
          {points.length === 0 ? (
            <Empty title={history.loading ? "Loading the trial log" : "No trials logged yet"} />
          ) : (
            <LuckChart points={points} />
          )}
          {validation.data && <p className="small secondary">{luckBar(validation.data.trials, validation.data.expected_max_sharpe)}</p>}
        </Panel>
      </Section>

      <Section title="What the evidence says" description="From research/findings.md.">
        <Panel>
          {!rules.data || rules.data.lessons.length === 0 ? (
            <Empty title={rules.loading ? "Loading findings" : "No findings written up yet"} />
          ) : (
            <ol className="lessons">
              {rules.data.lessons.map((l) => (
                <li key={l.title}>
                  <div className="lesson-title">{l.title}</div>
                  <p className="small secondary">{l.text}</p>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </Section>
    </>
  );
}
