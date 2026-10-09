import { MODE_NAMES, type FeedEntry } from "../api/playback";

export default function ExplanationFeed({ entries }: { entries: FeedEntry[] }) {
  return <section className="explanation-card"><div className="section-heading"><h2>Controller decisions</h2><span className="eyebrow">LATEST FIRST</span></div>
    {entries.length ? <ol className="feed">{entries.slice(0, 20).map(e => <li key={e.key} className={e.intersection_id ? "" : "session-event"}>
      <div><time>{e.t.toFixed(1)}s</time><span>{MODE_NAMES[e.mode]}</span></div><p>{e.text}</p>
    </li>)}</ol> : <p className="empty-feed">AI explanations will appear here as the simulation runs.</p>}
  </section>;
}
