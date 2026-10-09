import { useState } from "react";
import { api } from "../api";
import type { GeocodeResult } from "../types/contract.gen";

export default function SearchBox({ onResult, onError }: { onResult: (result: GeocodeResult) => void; onError: (message: string) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GeocodeResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const search = async (event: React.FormEvent) => {
    event.preventDefault(); if (!query.trim() || busy) return;
    setBusy(true);
    try { setResults((await api.geocode(query.trim())).results); } catch (error) { onError(String(error)); }
    finally { setBusy(false); }
  };
  return <div className="search-wrap"><form onSubmit={event => void search(event)} className="search-form" role="search">
    <span aria-hidden="true">⌕</span><input aria-label="Search for a place" placeholder="Search a place · press Enter" value={query} onChange={event => setQuery(event.target.value)} />
    {busy && <span className="spinner" aria-label="Searching" />}
  </form>{results !== null && <div className="search-results">{results.length ? results.map((r, i) => <button key={i} onClick={() => { onResult(r); setResults(null); }}>{r.display_name}</button>) : <p>No places found. Try another name.</p>}<button className="muted-button" onClick={() => setResults(null)}>Dismiss results</button></div>}</div>;
}
