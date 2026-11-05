import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Regression guard. `useEffect(() => something())` returns whatever something() returns,
// and React calls a returned value as the cleanup on unmount. Chrome's scrollIntoView
// started returning a Promise, which crashed the Strategies page on a tab switch. Every
// effect must have a block body, so it only ever returns a cleanup on purpose.
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("React effects", () => {
  it("never return a value by accident", () => {
    const offenders = sources(join(__dirname, "..")).flatMap((file) =>
      readFileSync(file, "utf8")
        .split("\n")
        .map((line, i) => ({ line, at: `${file}:${i + 1}` }))
        .filter(({ line }) => /useEffect\(\(\) => [^{\s]/.test(line))
        .map(({ at }) => at),
    );
    expect(offenders).toEqual([]);
  });
});
