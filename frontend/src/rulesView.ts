// Plain logic behind the Rules page, kept out of components so it can be tested directly.

/** A finding's title, with a trailing parenthetical aside (a date, a command, "a study")
 * split off so the claim reads on one line: "X (a study, 2026-11-18)." -> X., aside. */
export function splitTitle(title: string): { claim: string; aside: string | null } {
  const m = /^(.*\S)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*(\.?)$/.exec(title.trim());
  const [, claim = "", aside = "", stop = ""] = m ?? [];
  if (claim.length < 12) return { claim: title.trim(), aside: null };
  return { claim: `${claim}${stop}`, aside };
}
