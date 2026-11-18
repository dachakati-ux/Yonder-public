"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { GatewayError, completeChat, listModels } from "@/lib/gateway";

type SideResult = {
  model: string;
  content: string;
  durationMs: number;
  requestId: string | null;
  error?: string;
};

export function ComparePlayground() {
  const [models, setModels] = useState<string[]>([]);
  const [left, setLeft] = useState("yonder-chat");
  const [right, setRight] = useState("yonder-chat");
  const [prompt, setPrompt] = useState(
    "In three bullets, how should Yonder report unavailable GPU metrics?",
  );
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<SideResult[]>([]);

  useEffect(() => {
    let cancelled = false;
    void listModels()
      .then((cards) => {
        if (cancelled) return;
        const ids = cards.map((m) => m.id);
        setModels(ids.length ? ids : ["yonder-chat"]);
        if (ids[0]) setLeft(ids[0]);
        if (ids[1]) setRight(ids[1]);
        else if (ids[0]) setRight(ids[0]);
      })
      .catch(() => {
        if (!cancelled) setModels(["yonder-chat"]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onRun() {
    setBusy(true);
    setResults([]);
    const runOne = async (model: string): Promise<SideResult> => {
      try {
        const out = await completeChat({
          model,
          messages: [{ role: "user", content: prompt }],
          max_tokens: 300,
        });
        return {
          model,
          content: out.content,
          durationMs: out.durationMs,
          requestId: out.requestId,
        };
      } catch (err) {
        return {
          model,
          content: "",
          durationMs: 0,
          requestId: null,
          error:
            err instanceof GatewayError
              ? `${err.message} [${err.code}]`
              : err instanceof Error
                ? err.message
                : "failed",
        };
      }
    };
    const [a, b] = await Promise.all([runOne(left), runOne(right)]);
    setResults([a, b]);
    setBusy(false);
  }

  return (
    <div className="lab-layout">
      <header className="models-header">
        <div>
          <p className="empty-kicker">
            <Link href="/lab">Lab</Link> / Compare
          </p>
          <h1>Side-by-side compare</h1>
          <p>
            Same prompt to two aliases via Gateway. Latency is client wall time
            (ledger ttft stays not_reported unless returned separately).
          </p>
        </div>
      </header>
      <section className="lab-panel">
        <div className="lab-mode-row">
          <label>
            Left
            <select
              className="status-select"
              value={left}
              onChange={(e) => setLeft(e.target.value)}
            >
              {models.map((id) => (
                <option key={`l-${id}`} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
          <label>
            Right
            <select
              className="status-select"
              value={right}
              onChange={(e) => setRight(e.target.value)}
            >
              {models.map((id) => (
                <option key={`r-${id}`} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
        </div>
        <textarea
          className="lab-input"
          rows={4}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => void onRun()}
        >
          {busy ? "Comparing…" : "Compare"}
        </button>
        <div className="lab-grid">
          {results.map((row) => (
            <article key={row.model} className="lab-card">
              <div className="lab-card-kicker">{row.model}</div>
              <p>
                client_ms={row.durationMs.toFixed(0)}
                {row.requestId ? ` · ${row.requestId}` : ""}
              </p>
              {row.error ? (
                <div className="message-error">{row.error}</div>
              ) : (
                <pre className="lab-json">{row.content}</pre>
              )}
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
