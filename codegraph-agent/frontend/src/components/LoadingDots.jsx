export default function LoadingDots({ label }) {
  return (
    <span className="loading-dots" role="status" aria-live="polite">
      {label && <span className="loading-label">{label}</span>}
      <span className="dot" />
      <span className="dot" />
      <span className="dot" />
    </span>
  );
}