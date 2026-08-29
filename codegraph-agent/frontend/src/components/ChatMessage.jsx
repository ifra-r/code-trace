import CitationChip from "./CitationChip.jsx";
import LoadingDots from "./LoadingDots.jsx";

export default function ChatMessage({ role, text, chunks, error, pending }) {
  if (role === "question") {
    return (
      <div className="message-row question-row">
        <div className="bubble question-bubble">{text}</div>
      </div>
    );
  }

  return (
    <div className="message-row answer-row">
      <div className={`bubble answer-bubble${error ? " answer-error" : ""}`}>
        {pending && <LoadingDots label="Thinking" />}

        {!pending && error && <p className="answer-error-text">{error}</p>}

        {!pending && !error && chunks?.length === 0 && (
          <p className="answer-empty-text">
            No grounded evidence found for that in the indexed repository.
          </p>
        )}

        {!pending &&
          !error &&
          chunks?.map((chunk, i) => (
            <p className="answer-chunk" key={i}>
              {chunk.text}{" "}
              {chunk.nodes.map((node) => (
                <CitationChip node={node} key={node.id} />
              ))}
            </p>
          ))}
      </div>
    </div>
  );
}