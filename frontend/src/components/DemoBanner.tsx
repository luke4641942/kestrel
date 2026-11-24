import { useEffect, useState } from "react";
import { demoMeta, type DemoMeta } from "../demo";

/** Says what the public demo is: a read-only snapshot of the real platform, and of when. */
export function DemoBanner({ load = demoMeta }: { load?: () => Promise<DemoMeta | undefined> }) {
  const [meta, setMeta] = useState<DemoMeta>();
  useEffect(() => {
    let alive = true;
    void load().then((m) => alive && setMeta(m));
    return () => {
      alive = false;
    };
  }, [load]);
  const when = meta ? new Date(meta.exported_at).toLocaleDateString("en-AU", { dateStyle: "medium" }) : undefined;
  return (
    <aside className="demo-banner" role="note" aria-label="About this demo">
      <strong>Read-only demo.</strong> A snapshot of the real research platform
      {when && <> taken {when}</>}
      {meta?.commit && <> (commit <code>{meta.commit}</code>)</>}: every research note, result,
      backtest and paper account as the local dashboard shows them. Running backtests, editing
      notes and the research chat need the app running locally.{" "}
      <a href="https://github.com/luke4641942/kestrel">Source on GitHub</a>
    </aside>
  );
}
