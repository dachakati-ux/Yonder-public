"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { GatewayError, completeChat, listModels } from "@/lib/gateway";
import { EVAL_PROMPTS, type EvalCase } from "./evalPack";

type EvalRow = {
  id: string;
  title: string;
  outcome: "pass" | "fail" | "error";
  detail: string;
  durationMs: number;
};

export function EvalPlayground() {
  const [model, setModel] = useState("yonder-chat");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<EvalRow[]>([]);

  useEffect(() => {
    let cancelled = false;
    void listModels()
      .then((cards) => {
        if (cancelled) return;
        const ids = cards.map((m) => m.id);
        setModels(ids.length ? ids : ["yonder-chat"]);
        if (ids[0]) setModel(ids[0]);
      })
      .catch(() => {
        if (!cancelled) setModels(["yonder-chat"]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function runCase(item: EvalCase): Promise<EvalRow> {
    const started = performance.now();
    try {
      const out = await completeChat({
        model,
        messages: [
          ...(item.system
            ? [{ role: "system" as const, content: item.system }]
            : []),
          { role: "user", content: item.prompt },
        ],
        max_tokens: 200,
      });
      const hay = out.content.toLowerCase();
      const ok = item.expectSubstrings.every((s) =>
        hay.includes(s.toLowerCase()),
      );
      return {
        id: item.id,
        title: item.title,
        outcome: ok ? "pass" : "fail",
        detail: ok
          ? "expected substrings present"
          : `missing one of: ${item.expectSubstrings.join(", ")}`,
        durationMs: performance.now() - started,
      };
    } catch (err) {
      return {
        id: item.id,
        title: item.title,
        outcome: "error",
        detail:
          err instanceof GatewayError
            ? `${err.message} [${err.code}]`
            : err instanceof Error
              ? err.message
              : "error",
        durationMs: performance.now() - started,
      };
    }
  }

  async function onRun() {
    setBusy(true);
    const next: EvalRow[] = [];
    for (const item of EVAL_PROMPTS) {
      next.push(await runCase(item));
      setRows([...next]);
    }
    setBusy(false);
  }

  return (
    <div className="lab-layout">
      <header className="models-header">
        <div>
          <p className="empty-kicker">
            <Link href="/lab">Lab</Link> / Eval pack
          </p>
          <h1>Light eval / quiz</h1>
          <p>
            Fixed prompt pack with substring checks. Outcomes are measured from
            Gateway responses — never invented scores.
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
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => void onRun()}
        >
          {busy ? "Running pack…" : "Run eval pack"}
        </button>
        <table className="ops-table">
          <thead>
            <tr>
              <th>id</th>
              <th>title</th>
              <th>outcome</th>
              <th>client_ms</th>
              <th>detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.id}</td>
                <td>{row.title}</td>
                <td>{row.outcome}</td>
                <td>{row.durationMs.toFixed(0)}</td>
                <td>{row.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
