// A small Markdown renderer for Kestrel's own documents (docs/research-summary.md): headings,
// paragraphs, bullet lists, tables, **bold**, *italics*, `code` and [links](url). Written here rather
// than added as a dependency because it only ever renders our own files. It builds React
// elements, never HTML strings, so nothing in a file can inject markup. Only http(s) links
// become anchors; links to repo files (relative paths) show as their text.
import type { ReactNode } from "react";

export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "table"; head: string[]; rows: string[][] };

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "bold"; text: string }
  | { kind: "italic"; text: string }
  | { kind: "code"; text: string }
  | { kind: "link"; text: string; href: string };

const cells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());

const isRule = (line: string) => /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$/.test(line.trim());

/** The document's blocks, in order. */
export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const at = (k: number) => lines[k] ?? "";
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = at(i);
    if (!line.trim()) {
      i++;
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      out.push({ kind: "heading", level: (heading[1] ?? "#").length, text: (heading[2] ?? "").trim() });
      i++;
      continue;
    }
    if (line.trim().startsWith("|") && isRule(at(i + 1))) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && at(i).trim().startsWith("|")) rows.push(cells(at(i++)));
      out.push({ kind: "table", head, rows });
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && at(i).trim()) {
        const item = /^\s*[-*]\s+(.*)$/.exec(at(i));
        if (item) items.push((item[1] ?? "").trim());
        else items[items.length - 1] += ` ${at(i).trim()}`; // a wrapped item
        i++;
      }
      out.push({ kind: "list", items });
      continue;
    }
    const text: string[] = [];
    while (i < lines.length && at(i).trim() && !/^(#{1,4}\s|\s*[-*]\s|\|)/.test(at(i))) {
      text.push(at(i++).trim());
    }
    out.push({ kind: "paragraph", text: text.join(" ") });
  }
  return out;
}

/** Bold, code and links within a line of text. */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const pattern = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\*([^*\s][^*]*?)\*/g;
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index > last) out.push({ kind: "text", text: text.slice(last, m.index) });
    if (m[1] !== undefined) out.push({ kind: "bold", text: m[1] });
    else if (m[2] !== undefined) out.push({ kind: "code", text: m[2] });
    else if (m[5] !== undefined) out.push({ kind: "italic", text: m[5] });
    else out.push({ kind: "link", text: m[3] ?? "", href: m[4] ?? "" });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

/** One line of text with its bold, code and links. */
export function InlineText({ text }: { text: string }) {
  return (
    <>
      {parseInline(text).map((p, k) => {
        switch (p.kind) {
          case "bold":
            return <strong key={k}>{p.text}</strong>;
          case "italic":
            return <em key={k}>{p.text}</em>;
          case "code":
            return <code key={k}>{p.text}</code>;
          case "link":
            return /^https?:\/\//.test(p.href) ? (
              <a key={k} href={p.href} target="_blank" rel="noopener noreferrer">
                {p.text}
              </a>
            ) : (
              <code key={k}>{p.text}</code>
            );
          default:
            return <span key={k}>{p.text}</span>;
        }
      })}
    </>
  );
}

export function Markdown({ source, skipTitle = false }: { source: string; skipTitle?: boolean }) {
  const blocks = parseBlocks(source).filter((b, k) => !(skipTitle && k === 0 && b.kind === "heading" && b.level === 1));
  const nodes: ReactNode[] = blocks.map((b, k) => {
    switch (b.kind) {
      case "heading": {
        const Tag = `h${Math.min(b.level + 1, 4)}` as "h2" | "h3" | "h4";
        return (
          <Tag key={k}>
            <InlineText text={b.text} />
          </Tag>
        );
      }
      case "paragraph":
        return (
          <p key={k}>
            <InlineText text={b.text} />
          </p>
        );
      case "list":
        return (
          <ul key={k}>
            {b.items.map((item, j) => (
              <li key={j}>
                <InlineText text={item} />
              </li>
            ))}
          </ul>
        );
      case "table":
        return (
          <div key={k} className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {b.head.map((h, j) => (
                    <th key={j}>
                      <InlineText text={h} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((c, j) => (
                      <td key={j}>
                        <InlineText text={c} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
    }
  });
  return <div className="prose">{nodes}</div>;
}
