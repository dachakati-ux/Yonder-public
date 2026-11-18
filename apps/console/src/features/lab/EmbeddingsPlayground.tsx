"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { GatewayError, createEmbedding, listModels } from "@/lib/gateway";

function cosine(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length);
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < n; i += 1) {
    dot += a[i]! * b[i]!;
    na += a[i]! * a[i]!;
    nb += b[i]! * b[i]!;
  }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

export function EmbeddingsPlayground() {
  const [model, setModel] = useState("yonder-embed-4b");
  const [models, setModels] = useState<string[]>([]);
  const [a, setA] = useState("GPU placement and context length limits");
  const [b, setB] = useState("Workers register with the Gateway using stable aliases");
  const [c, setC] = useState("The cafeteria serves tomato soup on Fridays");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dims, setDims] = useState<number | null>(null);
  const [scores, setScores] = useState<{ ab: number; ac: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listModels()
      .then((cards) => {
        if (cancelled) return;
        const ids = cards.map((m) => m.id).filter((id) => id.includes("embed"));
        setModels(ids.length ? ids : ["yonder-embed-4b", "yonder-embed-8b"]);
        if (ids[0]) setModel(ids[0]);
      })
      .catch(() => {
        if (!cancelled) setModels(["yonder-embed-4b", "yonder-embed-8b"]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onRun() {
    setBusy(true);
    setError(null);
    setScores(null);
    try {
      const body = await createEmbedding({ model, input: [a, b, c] });
      const vectors = body.data
        .slice()
        .sort((x, y) => x.index - y.index)
        .map((row) => row.embedding);
      if (vectors.length < 3) {
        setError("Gateway returned fewer than 3 embeddings");
        return;
      }
      setDims(vectors[0]!.length);
      setScores({
        ab: cosine(vectors[0]!, vectors[1]!),
        ac: cosine(vectors[0]!, vectors[2]!),
      });
    } catch (err) {
      setError(
        err instanceof GatewayError
          ? `${err.message} [${err.code}]`
          : err instanceof Error
            ? err.message
            : "Embedding failed",
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
            <Link href="/lab">Lab</Link> / Embeddings playground
          </p>
          <h1>Embeddings playground</h1>
          <p>
            Compare three texts via Gateway <code>/v1/embeddings</code>. Similar
            ops text should score above an unrelated distractor.
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
        <textarea className="lab-input" rows={3} value={a} onChange={(e) => setA(e.target.value)} />
        <textarea className="lab-input" rows={3} value={b} onChange={(e) => setB(e.target.value)} />
        <textarea className="lab-input" rows={3} value={c} onChange={(e) => setC(e.target.value)} />
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => void onRun()}
        >
          {busy ? "Embedding…" : "Embed + compare"}
        </button>
        {error ? <div className="message-error">{error}</div> : null}
        {dims != null && scores ? (
          <div className="lab-result">
            <p>dimensions: {dims}</p>
            <p>
              cosine(A,B)={scores.ab.toFixed(4)} · cosine(A,C)={scores.ac.toFixed(4)}
            </p>
            <p>
              sanity:{" "}
              {scores.ab > scores.ac
                ? "pass (related &gt; unrelated)"
                : "soft_fail (related not above unrelated)"}
            </p>
          </div>
        ) : null}
      </section>
    </div>
  );
}
