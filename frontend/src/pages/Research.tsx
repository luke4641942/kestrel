import { useMemo, useState } from "react";
import { api } from "../api";
import { LuckChart } from "../components/LuckChart";
import { Empty, PageHeader, Panel, Section, Stat, Status } from "../components/ui";
import { date } from "../format";
import { useResource } from "../hooks";
import { byFamily, confirmationRuns, luckSeries, tally } from "../researchView";
import { DECISION, luckBar, poolsNote, studyTone } from "../summary";
import { MarketBadge, useMarket } from "../market";
import { ASSET_CLASSES, LABEL, MARKETS, cellText, classOf, inMarket, marketGrid, poolBar, poolLine } from "../markets";

const pct = (x: number) => `${Math.round(x * 100)}%`;
const sharpe = (x: number | null) => (x === null ? "–" : x.toFixed(2));

// Everything here is read from the API (research/decisions.toml, the trial log and
// research/findings.md); the page only groups and draws it.
export function Research() {
  const decisions = useResource(api.decisions, []);
  const notes = useResource(api.researchList, []);
  const { market, setMarket } = useMarket();
  const validation = useResource(api.validation, []);
  const history = useResource(api.validationHistory, []);
  const rules = useResource(api.rules, []);
  const [open, setOpen] = useState<string | null>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);

  const shown = useMemo(
    () => (decisions.data ?? []).filter((d) => inMarket(d, market)),
    [decisions.data, market],
  );
  const rows = useMemo(() => byFamily(shown), [shown]);
  const grid = useMemo(
    () => marketGrid(decisions.data ?? [], notes.data ?? []),
    [decisions.data, notes.data],
  );
  const points = useMemo(() => luckSeries(history.data ?? []), [history.data]);
  const runs = useMemo(() => confirmationRuns(validation.data?.studies ?? []), [validation.data]);
  const all = shown;
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
          {market === "all" || market === "crypto" ? (
            <>
              <Stat
                label="Trials logged"
                value={validation.data ? validation.data.trials.toLocaleString("en-AU") : "–"}
                note={validation.data ? poolsNote(validation.data.pools, validation.data.all_trials) : undefined}
              />
              <Stat
                label="Luck bar"
                value={validation.data ? validation.data.expected_max_sharpe.toFixed(2) : "–"}
                note="annual Sharpe, crypto pool"
              />
            </>
          ) : (
            <>
              <Stat
                label={`Trials logged, ${LABEL[market].toLowerCase()}`}
                value={(validation.data?.pools?.[market === "forex" ? "forex" : "futures"] ?? 0).toLocaleString("en-AU")}
                note={validation.data ? `${validation.data.all_trials ?? validation.data.trials} across all pools` : undefined}
              />
              <Stat label="Luck bar" value={poolBar(validation.data, market)} note="annual Sharpe, its own pool" />
            </>
          )}
        </div>
      </Panel>

      <Section
        title="Which family, in which market"
        tour="market-grid"
        description="Families down, markets across: the best decision in each, and notes still open. Each market is judged against its own luck bar; a good cell nobody predicted is a lead for a new note, not a finding."
      >
        <Panel>
          {grid.length === 0 ? (
            <Empty title={decisions.loading ? "Loading" : "Nothing tried yet"} />
          ) : (
            <div className="table-wrap">
              <table className="table market-grid">
                <thead>
                  <tr>
                    <th>Family</th>
                    {ASSET_CLASSES.map((c) => (
                      <th key={c}>
                        <button type="button" className="link-button" onClick={() => setMarket(c)}>
                          {LABEL[c]}
                        </button>
                        <div className="small secondary" style={{ fontWeight: 400 }}>
                          {MARKETS.find((m) => m.id === c)?.parked ? "parked" : poolLine(validation.data, c)}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {grid.map((r) => (
                    <tr key={r.family.key}>
                      <td>
                        <span style={{ fontWeight: 500 }}>{r.family.name}</span>
                      </td>
                      {ASSET_CLASSES.map((c) => {
                        const cell = r.cells[c];
                        const tried = cell.decisions.length > 0 || cell.open > 0;
                        return (
                          <td key={c} className={tried ? undefined : "untried"}>
                            {cell.best ? (
                              <Status tone={DECISION[cell.best].tone}>{cellText(cell)}</Status>
                            ) : (
                              cellText(cell)
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </Section>

      <Section
        title="What has been tried, by family"
        tour="families"
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
                                    <span style={{ fontWeight: 500 }}>{d.name}</span> <MarketBadge cls={classOf(d)} />{" "}
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

      {runs.length > 0 && (
        <Section
          title="The exploration experiment"
          tour="exploration"
          description="Rules found by searching freely on half the altcoins (not evidence), then each run once on the other half, which nobody had looked at. Passing needs the strategy to beat holding on more than half the coins and in more than half the walk-forward windows. These are summaries, not saved backtests: one look each in their own trial pool."
        >
          <Panel>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Graduate</th>
                    <th className="num">Beats holding</th>
                    <th className="num">Windows won</th>
                    <th className="num">Median Sharpe</th>
                    <th>Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => {
                    const isOpen = openRun === r.id;
                    const toggle = () => setOpenRun(isOpen ? null : r.id);
                    return [
                      <tr key={r.id} className="clickable" onClick={toggle}>
                        <td>
                          <button
                            type="button"
                            className="link-button"
                            aria-expanded={isOpen}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggle();
                            }}
                          >
                            {r.strategy}
                          </button>
                          <div className="small secondary mono">{r.note}</div>
                        </td>
                        <td className="num nw">
                          {Math.round(r.beatsShare * r.coins)} of {r.coins} ({pct(r.beatsShare)})
                        </td>
                        <td className="num nw">{pct(r.windowsShare)} of {r.windows}</td>
                        <td className="num">{r.medianSharpe === null ? "–" : r.medianSharpe.toFixed(2)}</td>
                        <td className="nw">
                          <Status tone={studyTone(r.verdict)}>{r.verdict}</Status>
                        </td>
                      </tr>,
                      isOpen && (
                        <tr key={`${r.id}-coins`}>
                          <td colSpan={5}>
                            <table className="table coin-table">
                              <thead>
                                <tr>
                                  <th>Coin</th>
                                  <th className="num">Sharpe</th>
                                  <th className="num">Holding</th>
                                  <th className="num">Ahead by</th>
                                  <th className="num">Windows won</th>
                                </tr>
                              </thead>
                              <tbody>
                                {r.perCoin.map((c) => (
                                  <tr key={c.symbol}>
                                    <td className="mono small">{c.symbol}</td>
                                    <td className="num">{sharpe(c.sharpe)}</td>
                                    <td className="num">{sharpe(c.heldSharpe)}</td>
                                    <td className="num">{sharpe(c.gap)}</td>
                                    <td className="num">{c.windows ? `${c.windowsBeating}/${c.windows}` : "–"}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      ),
                    ];
                  })}
                </tbody>
              </table>
            </div>
          </Panel>
        </Section>
      )}

      <Section
        title="The luck bar over time"
        tour="luck"
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
