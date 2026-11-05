import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../api";
import { JobsDock } from "./JobsDock";

const NOW = new Date("2026-11-05T00:00:00Z").getTime();

function job(over: Partial<Job> = {}): Job {
  return {
    id: "abc123",
    kind: "ingest",
    params: { symbol: "BTCUSDT", interval: "4h", start: "2024-01", end: "2024-03" },
    status: "running",
    progress: 0.42,
    message: "Downloading 2024-02 (2/3)",
    logs: [],
    result: null,
    error: null,
    created_at: new Date(NOW - 5000).toISOString(),
    started_at: new Date(NOW - 5000).toISOString(),
    finished_at: null,
    ...over,
  };
}

function dock(jobs: Job[]) {
  return render(<JobsDock jobs={jobs} open={false} setOpen={() => {}} onChange={() => {}} />);
}

describe("JobsDock pill", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it("shows a running job's progress", () => {
    dock([job()]);
    expect(screen.getByRole("button")).toHaveTextContent("Ingest BTCUSDT 4h, 2024-01 to 2024-03");
    expect(screen.getByRole("button")).toHaveTextContent("42%");
  });

  it("summarises several running jobs", () => {
    dock([job({ id: "a" }), job({ id: "b" })]);
    expect(screen.getByRole("button")).toHaveTextContent("2 jobs running");
  });

  it("shows a finished job's result for 125 seconds, then shrinks", () => {
    const done = job({ status: "succeeded", progress: 1, finished_at: new Date(NOW).toISOString() });
    dock([done]);
    const pill = screen.getByRole("button");
    expect(pill).toHaveTextContent("Succeeded");
    expect(pill).not.toHaveClass("compact");

    act(() => vi.advanceTimersByTime(124_000));
    expect(screen.getByRole("button")).toHaveTextContent("Succeeded");

    act(() => vi.advanceTimersByTime(2_000));
    expect(screen.getByRole("button")).toHaveClass("compact");
    expect(screen.getByRole("button")).toHaveTextContent(/^Jobs$/);
  });

  it("starts compact when the last job finished long ago", () => {
    dock([job({ status: "succeeded", finished_at: new Date(NOW - 10 * 60_000).toISOString() })]);
    expect(screen.getByRole("button")).toHaveClass("compact");
  });

  it("keeps a red dot on the compact pill after a failure", () => {
    const { container } = dock([job({ status: "failed", finished_at: new Date(NOW - 10 * 60_000).toISOString() })]);
    expect(container.querySelector(".status.bad")).not.toBeNull();
  });

  it("says Jobs when there are none", () => {
    dock([]);
    expect(screen.getByRole("button")).toHaveTextContent("Jobs");
  });
});
