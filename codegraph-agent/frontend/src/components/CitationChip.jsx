import { useState } from "react";

export default function CitationChip({ node }) {
  const [open, setOpen] = useState(false);
  if (!node) return null;

  const lineRange =
    node.start_line === node.end_line
      ? `${node.start_line}`
      : `${node.start_line}-${node.end_line}`;

  return (
    <span className="citation">
      <button
        type="button"
        className="citation-chip"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
      >
        <span className="citation-name">{node.name}</span>
        <span className="citation-loc">
          {node.file_path}:{lineRange}
        </span>
      </button>

      {open && (
        <div className="citation-source">
          <div className="citation-source-header">
            {node.file_path} · lines {lineRange}
          </div>
          <pre className="citation-source-code">
            {node.source_text.split("\n").map((line, i) => (
              <div className="source-line" key={i}>
                <span className="source-line-no">{node.start_line + i}</span>
                <span className="source-line-text">{line}</span>
              </div>
            ))}
          </pre>
        </div>
      )}
    </span>
  );
}