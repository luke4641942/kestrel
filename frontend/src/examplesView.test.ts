import { describe, expect, it } from "vitest";
import type { Example } from "./api";
import { agreement, exampleTitle, ruleLine, testLines, weightLine } from "./examplesView";

const entry: Example = {
  open_time: "2020-07-04T00:00:00Z",
  kind: "entry",
  before: 0,
  after: 0.4924,
  close: 2.779,
  rule: "any",
  conditions: [
    { label: "ATOM up over 20 days", subject: "ATOMUSDT", value: 2.779, level: 2.756, met: true, move: 0 },
    { label: "ATOM up over 120 days", subject: "ATOMUSDT", value: 2.779, level: 3.854, met: false, move: 0 },
  ],
  agrees: true,
};

describe("worked examples", () => {
  it("says what was decided, when, and what it trades at", () => {
    expect(exampleTitle(entry)).toBe("Entry at the close of 2020-07-04 00:00 UTC");
    expect(exampleTitle({ ...entry, kind: "exit" })).toMatch(/^Exit/);
    expect(weightLine(entry)).toBe("Weight 0.00 → 0.49, close 2.779; trades at the next open");
  });

  it("lists every test with its values", () => {
    expect(testLines(entry)).toEqual([
      { text: "ATOM up over 20 days: ATOMUSDT 2.779 against 2.756", met: true },
      { text: "ATOM up over 120 days: ATOMUSDT 2.779 against 3.854", met: false },
    ]);
    expect(ruleLine(entry)).toBe("Sized by how many tests hold");
    expect(ruleLine({ ...entry, rule: "all" })).toBe("In the market when every test holds");
    expect(ruleLine({ ...entry, rule: null })).toBeNull();
  });

  it("flags a disagreement as a bug", () => {
    expect(agreement(entry).tone).toBe("ok");
    expect(agreement({ ...entry, agrees: false })).toEqual({ text: "Tests and weight disagree: a bug, report it", tone: "bad" });
    expect(agreement({ ...entry, agrees: null }).tone).toBe("neutral");
  });
});
