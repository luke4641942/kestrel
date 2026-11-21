import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useResource } from "./hooks";

function Probe({ fetcher, onFocus }: { fetcher: () => Promise<string>; onFocus: boolean }) {
  const r = useResource(fetcher, [], undefined, onFocus);
  return <div>{r.data ?? "none"}</div>;
}

describe("useResource", () => {
  it("fetches again when the tab comes back into view, if asked", async () => {
    let n = 0;
    const fetcher = vi.fn(() => Promise.resolve(`v${++n}`));
    render(<Probe fetcher={fetcher} onFocus />);
    expect(await screen.findByText("v1")).toBeInTheDocument();
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(await screen.findByText("v2")).toBeInTheDocument();
  });

  it("doesn't, by default", async () => {
    const fetcher = vi.fn(() => Promise.resolve("v"));
    render(<Probe fetcher={fetcher} onFocus={false} />);
    await screen.findByText("v");
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
