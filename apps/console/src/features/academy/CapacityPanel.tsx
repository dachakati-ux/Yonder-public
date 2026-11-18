"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  GatewayError,
  getCapacitySnapshot,
  type CapacitySnapshot,
} from "@/lib/gateway";
import { AcademySubnav } from "./AcademySubnav";

export function CapacityPanel() {
  const [data, setData] = useState<CapacitySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const body = await getCapacitySnapshot();
      setData(body);
      setError(null);
    } catch (err) {
      setError(
        err instanceof GatewayError
          ? `${err.message} [${err.code}]`
          : err instanceof Error
            ? err.message
            : "Failed to load capacity",
      );
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 30000);
    return () => window.clearInterval(id);
  }, [load]);

  return (
    <div className="academy-layout academy-layout-lively">
      <AcademySubnav />
      <header className="academy-hero academy-hero-lively">
        <div className="academy-hero-copy">
          <p className="empty-kicker academy-hero-kicker">Capacity</p>
          <h1>Readiness & funding literacy</h1>
          <p className="academy-hero-lead">
            Alias availability from live Gateway route state. Missing is{" "}
            <code>not_reported</code> — never invented READY.
          </p>
          <div className="academy-hero-actions">
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void load()}
            >
              {busy ? "Refreshing…" : "Refresh"}
            </button>
            <Link className="btn btn-ghost" href="/academy/lessons/capacity-cpu-vs-gpu">
              Capacity lessons
            </Link>
          </div>
        </div>
      </header>

      {error ? <div className="message-error models-error">{error}</div> : null}

      {data ? (
        <>
          <section className="academy-status-cards" id="capacity-aliases">
            <article className="academy-status-card">
              <p className="lab-card-kicker">Lab chat alias</p>
              <h2>{data.lab_runtime.chat_alias ?? "not_reported"}</h2>
              <p>
                Ready: {data.lab_runtime.chat_alias_ready ? "yes" : "no"} · Generation:{" "}
                {data.lab_runtime.generation_enabled ? "enabled" : "disabled"}
              </p>
            </article>
            {data.aliases.map((row) => (
              <article key={row.alias} className="academy-status-card" id={`alias-${row.alias}`}>
                <p className="lab-card-kicker">{row.alias}</p>
                <h2>{row.availability}</h2>
                <p>
                  In routes: {row.present_in_routes ? "yes" : "no"} · ready flag:{" "}
                  {row.ready ? "true" : "false"}
                </p>
              </article>
            ))}
          </section>

          <section className="academy-sandbox-grid">
            <div className="academy-panel">
              <h2>Fund CPU first</h2>
              <ul className="academy-objectives">
                {data.cpu_vs_gpu.cpu_first.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
            <div className="academy-panel">
              <h2>GPU when concurrent live try-it</h2>
              <ul className="academy-objectives">
                {data.cpu_vs_gpu.gpu_for.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          </section>
          <p className="muted">{data.note}</p>
        </>
      ) : !error ? (
        <p className="muted academy-skeleton">Loading capacity…</p>
      ) : null}
    </div>
  );
}
