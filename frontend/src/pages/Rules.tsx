import { api } from "../api";
import { Empty, PageHeader, Panel, Section } from "../components/ui";
import { date } from "../format";
import { useResource } from "../hooks";
import { luckBar } from "../summary";

// Everything here comes from the API (kestrel.validation.rules and research/findings.md),
// so the page can't drift from the rules the engine grades against.
export function Rules() {
  const rules = useResource(api.rules, []);
  const validation = useResource(api.validation, []);
  const r = rules.data;

  if (!r) {
    return (
      <>
        <PageHeader title="Rules" subtitle="How every strategy is judged." />
        <Panel>
          <Empty title={rules.error ? "Couldn't read the rules" : "Loading the rules"}>
            {rules.error && <p>{rules.error}</p>}
          </Empty>
        </Panel>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Rules" subtitle={`How every strategy is judged. Last reviewed ${date(r.reviewed)}.`} />

      <Section title="What counts as a pass">
        <Panel pad>
          <p className="rules-lead">{r.passing}</p>
          {validation.data && (
            <p className="small secondary rules-luck">
              {luckBar(validation.data.trials, validation.data.expected_max_sharpe)}
            </p>
          )}
        </Panel>
      </Section>

      <Section
        title="The scorecard"
        description="Every strategy, on data before the holdout. A research note may add tests but never remove one."
      >
        <Panel>
          <ol className="scorecard">
            {r.scorecard.map((t) => (
              <li key={t.name}>
                <span className="scorecard-name">{t.name}</span>
                <div>
                  <div className="scorecard-rule">{t.rule}</div>
                  <p className="small secondary">{t.why}</p>
                </div>
              </li>
            ))}
          </ol>
        </Panel>
      </Section>

      <div className="rules-pair">
        <Section title="Before a new idea is tested" description="Its research note must state:">
          <Panel pad>
            <ul className="rules-list">
              {r.entry_bar.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </Panel>
        </Section>
        <Section title="Risk-managed holding">
          <Panel pad>
            <p className="rules-text">{r.risk_managed}</p>
            <p className="rules-text secondary">{r.account}</p>
          </Panel>
        </Section>
      </div>

      <Section
        title="What the research has found"
        description="From research/findings.md. Every strategy's decision and reason is on the Overview."
      >
        <Panel>
          {r.lessons.length === 0 ? (
            <Empty title="No findings written up yet" />
          ) : (
            <ol className="lessons">
              {r.lessons.map((l) => (
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
