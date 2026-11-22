import { describe, expect, it } from "vitest";
import { splitTitle } from "./rulesView";

describe("splitTitle", () => {
  it("splits a trailing aside off a finding's title", () => {
    expect(splitTitle("A coin's first perp listing marks a local top (a study, 2026-11-18, `kestrel event-study`).")).toEqual({
      claim: "A coin's first perp listing marks a local top.",
      aside: "a study, 2026-11-18, `kestrel event-study`",
    });
    expect(splitTitle("Large unlocks don't drift (a study, 2026-11-21, note x (v2)).")).toEqual({
      claim: "Large unlocks don't drift.",
      aside: "a study, 2026-11-21, note x (v2)",
    });
  });

  it("leaves a title without one alone", () => {
    expect(splitTitle("Stops made the survivors worse.")).toEqual({ claim: "Stops made the survivors worse.", aside: null });
    expect(splitTitle("(Only an aside)")).toEqual({ claim: "(Only an aside)", aside: null });
  });
});
