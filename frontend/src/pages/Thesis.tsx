import { api } from "../api";
import { Empty, PageHeader, Panel } from "../components/ui";
import { useResource } from "../hooks";
import { Markdown } from "../markdown";

// The one-page research summary, from docs/research-summary.md via the API, so the page and
// the file in the repo can't drift apart.
export function Thesis() {
  const summary = useResource(api.summary, []);
  return (
    <>
      <PageHeader
        title="Thesis"
        subtitle="What the research has found, on one page: how a result is judged, what's being paper-traded and why it isn't proven, and what failed."
      />
      <Panel pad>
        {summary.data ? (
          <Markdown source={summary.data.markdown} skipTitle />
        ) : (
          <Empty title={summary.error ? "Couldn't read the summary" : "Loading the summary"}>
            {summary.error && <p>{summary.error}</p>}
          </Empty>
        )}
      </Panel>
    </>
  );
}
