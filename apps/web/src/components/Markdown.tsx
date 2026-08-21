import type { ReactNode } from "react";

// Minimal renderer for exactly the markdown shapes
// apps/api/src/targetforge/agents/report_agent.py's `_render_markdown`
// produces (#/##/###, **bold**, [text](url) links, blockquotes, list items,
// paragraphs) — not a general-purpose markdown library. Builds React
// elements directly rather than `dangerouslySetInnerHTML`, so there's no
// HTML-injection surface even though the source is our own trusted,
// LLM-authored-but-schema-constrained content.

function renderInline(text: string): ReactNode[] {
  // Handles **bold** and [text](url) in one left-to-right pass; the two
  // never nest in this report's actual output, so a single combined regex
  // is enough — no need for a real recursive inline parser.
  const pattern = /(\*\*(.+?)\*\*)|(\[([^\]]+)\]\(([^)]+)\))/g;
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    if (match[1]) {
      nodes.push(<strong key={key++}>{match[2]}</strong>);
    } else {
      nodes.push(
        <a key={key++} href={match[5]} target="_blank" rel="noreferrer">
          {match[4]}
        </a>,
      );
    }
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];
  let key = 0;

  function flushList() {
    if (listItems.length === 0) return;
    blocks.push(
      <ul key={key++}>
        {listItems.map((item, i) => (
          <li key={i}>{renderInline(item)}</li>
        ))}
      </ul>,
    );
    listItems = [];
  }

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (line.startsWith("- ")) {
      listItems.push(line.slice(2));
      continue;
    }
    flushList();

    if (line.startsWith("### ")) {
      blocks.push(<h4 key={key++}>{renderInline(line.slice(4))}</h4>);
    } else if (line.startsWith("## ")) {
      blocks.push(<h3 key={key++}>{renderInline(line.slice(3))}</h3>);
    } else if (line.startsWith("# ")) {
      blocks.push(<h2 key={key++}>{renderInline(line.slice(2))}</h2>);
    } else if (line.startsWith("> ")) {
      blocks.push(<blockquote key={key++}>{renderInline(line.slice(2))}</blockquote>);
    } else if (line.startsWith("*") && !line.startsWith("**") && line.endsWith("*") && line.length > 1) {
      // Single-asterisk whole-line italic (e.g. "*Reference build: ...*") —
      // deliberately excludes "**bold**" lines like "**Literature
      // evidence:**", which fall through to the default paragraph branch
      // below and get their bold rendered by renderInline() instead.
      blocks.push(
        <p key={key++} className="md-italic">
          {line.slice(1, -1)}
        </p>,
      );
    } else if (line.trim() === "") {
      // paragraph break — no node needed, CSS margins do the spacing
    } else {
      blocks.push(<p key={key++}>{renderInline(line)}</p>);
    }
  }
  flushList();

  return <div className="markdown">{blocks}</div>;
}
