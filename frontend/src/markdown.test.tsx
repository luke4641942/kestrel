import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown, parseBlocks, parseInline } from "./markdown";

const DOC = `# Title

Intro **bold** with \`code\` and a [paper](https://example.com/p) and a [file](../research/findings.md).

## How

- One item
  that wraps.
- Two

| A | B |
|---|---|
| 1 | **2** |
`;

describe("markdown", () => {
  it("reads headings, wrapped list items, paragraphs and tables", () => {
    expect(parseBlocks(DOC)).toEqual([
      { kind: "heading", level: 1, text: "Title" },
      { kind: "paragraph", text: expect.stringContaining("Intro **bold**") },
      { kind: "heading", level: 2, text: "How" },
      { kind: "list", items: ["One item that wraps.", "Two"] },
      { kind: "table", head: ["A", "B"], rows: [["1", "**2**"]] },
    ]);
  });

  it("reads italics, but not a bold's asterisks or a lone one", () => {
    expect(parseInline("beat *the same coin* held, 2 * 3")).toEqual([
      { kind: "text", text: "beat " },
      { kind: "italic", text: "the same coin" },
      { kind: "text", text: " held, 2 * 3" },
    ]);
  });

  it("reads bold, code and links inside a line", () => {
    expect(parseInline("a **b** `c` [d](e)")).toEqual([
      { kind: "text", text: "a " },
      { kind: "bold", text: "b" },
      { kind: "text", text: " " },
      { kind: "code", text: "c" },
      { kind: "text", text: " " },
      { kind: "link", text: "d", href: "e" },
    ]);
  });

  it("renders web links as links, repo paths as text, and never raw HTML", () => {
    render(<Markdown source={`${DOC}\n<script>alert(1)</script> <b>x</b>\n`} skipTitle />);
    expect(screen.queryByText("Title")).toBeNull();
    expect(screen.getByRole("link", { name: "paper" })).toHaveAttribute("href", "https://example.com/p");
    expect(screen.queryByRole("link", { name: "file" })).toBeNull();
    expect(screen.getByText("file").tagName).toBe("CODE");
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument(); // shown as text
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("refuses javascript: links", () => {
    render(<Markdown source="[x](javascript:alert(1))" />);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
