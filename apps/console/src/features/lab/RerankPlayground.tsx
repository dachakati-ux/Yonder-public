"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { GatewayError, createRerank, listModels } from "@/lib/gateway";

const DEFAULT_DOCS = [
  "Yonder placement_limits clamp context by GPU count and VRAM headroom on V100 workers.",
  "The cafeteria menu includes salad and soup on Fridays.",
  "Workers register with the control plane; clients use stable model aliases only.",
  "Change windows are Tuesday and Thursday evenings for lab control-plane work.",
];

export function RerankPlayground() {
  const [model, setModel] = useState("yonder-rerank-4b");
  const [models, setModels] = useState<string[]>([]);
  const [query, setQuery] = useState(
    "GPU placement and context length limits for V100 workers",
  );
  const [docsText, setDocsText] = useState(DEFAULT_DOCS.join("\n---\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<
    Array<{ index: number; relevance_score: number; document?: string }>
  >([]);

  useEffect(() => {
    let cancelled = false;
    void listModels()
      .then((cards) => {
        if (cancelled) return;
        const ids = cards.map((m) => m.id).filter((id) => id.includes("rerank"));
        setModels(ids.length ? ids : ["yonder-rerank-4b"]);
        if (ids[0]) setModel(ids[0]);
      })
      .catch(() => {
        if (!cancelled) setModels(["yonder-rerank-4b"]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onRun() {
    setBusy(true);
    setError(null);
    try {
      const documents = docsText
        .split(/\n---\n/)
        .map((d) => d.trim())
        .filter(Boolean);
      const body = await createRerank({
        model,
        query,
        documents,
        top_n: documents.length,
      });
      setResults(body.results || []);
    } catch (err) {
      setError(
        err instanceof GatewayError
          ? `${err.message} [${err.code}]`
          : err instanceof Error
            ? err.message
            : "Rerank failed",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="lab-layout">
      <header className="models-header">
        <div>
          <p className="empty-kicker">
            <Link href="/lab">Lab</Link> / Rerank sandbox
          </p>
          <h1>Rerank sandbox</h1>
          <p>
            Rank documents for an ops query via Gateway <code>/v1/rerank</code>.
          </p>
        </div>
      </header>
      <section className="lab-panel">
        <label>
          Model alias
          <select
            className="status-select"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            {models.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
        <textarea
          className="lab-input"
          rows={2}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <textarea
          className="lab-input"
          rows={10}
          value={docsText}
          onChange={(e) => setDocsText(e.target.value)}
          aria-label="Documents separated by ---"
        />
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => void onRun()}
        >
          {busy ? "Reranking…" : "Rerank"}
        </button>
        {error ? <div className="message-error">{error}</div> : null}
        {results.length ? (
          <ol className="lab-steps-list">
            {results.map((row) => (
              <li key={`${row.index}-${row.relevance_score}`}>
                score={row.relevance_score.toFixed(4)} · idx={row.index}
                {row.document ? ` — ${row.document.slice(0, 120)}` : ""}
              </li>
            ))}
          </ol>
        ) : null}
      </section>
    </div>
  );
}
