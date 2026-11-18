"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  GatewayError,
  addSandboxDocument,
  addSandboxPart,
  createSandbox,
  listSandboxPresets,
  listSandboxes,
  resetSandbox,
  retrieveSandbox,
  type PracticeSandbox,
} from "@/lib/gateway";
import { AcademySubnav } from "./AcademySubnav";

const SAMPLE_QUERIES: Record<string, string[]> = {
  "site-ops-fixture": [
    "What does the SEL say about DIMM A1?",
    "lab-host-a inventory",
    "RMA warranty unknown",
  ],
  "support-fixture": [
    "What is the teaching severity guide?",
    "access policy VPN",
    "ticket triage honesty",
  ],
  "ops-fixture": [
    "When are teaching change windows?",
    "runbook freeze",
    "ops admission",
  ],
  "eng-fixture": [
    "What must clients use instead of worker URLs?",
    "Gateway-only alias",
    "placement honesty",
  ],
};

function presetFromLocation(): string {
  if (typeof window === "undefined") return "site-ops-fixture";
  return (
    new URLSearchParams(window.location.search).get("preset") ||
    "site-ops-fixture"
  );
}

function fromLessonFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("fromLesson");
}

function fromMissionFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("fromMission");
}

export function PracticeDesk() {
  const [preset, setPreset] = useState("site-ops-fixture");
  const [presets, setPresets] = useState<Array<{ id: string; label: string }>>(
    [],
  );
  const [sandbox, setSandbox] = useState<PracticeSandbox | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("My note");
  const [text, setText] = useState("");
  const [query, setQuery] = useState("What does the SEL say about DIMM A1?");
  const [hits, setHits] = useState<
    Array<{ title: string; snippet: string; score: number }>
  >([]);
  const [partNumber, setPartNumber] = useState("DIMM-A1-TEACH");
  const [incidentId, setIncidentId] = useState("inc-lab-host-a");
  const [busy, setBusy] = useState(false);
  const [fromLesson, setFromLesson] = useState<string | null>(null);
  const [fromMission, setFromMission] = useState<string | null>(null);

  const samples = SAMPLE_QUERIES[preset] ?? SAMPLE_QUERIES["site-ops-fixture"];
  const showParts = preset === "site-ops-fixture";

  const quotaHint = useMemo(() => {
    if (!sandbox) return null;
    return `Docs: ${sandbox.documents.length} · retrieve runs: ${sandbox.retrieve_runs}`;
  }, [sandbox]);

  async function ensureSandbox(nextPreset: string) {
    setBusy(true);
    setError(null);
    try {
      const listed = await listSandboxes();
      const existing = listed.sandboxes.find((s) => s.preset === nextPreset);
      if (existing) {
        setSandbox(existing);
      } else {
        const created = await createSandbox({
          label: nextPreset,
          preset: nextPreset,
        });
        setSandbox(created.sandbox);
      }
      setQuery(SAMPLE_QUERIES[nextPreset]?.[0] ?? "Retrieve teaching evidence");
    } catch (err) {
      if (err instanceof GatewayError) {
        const quarantined =
          err.code.toLowerCase().includes("quarantine") ||
          err.message.toLowerCase().includes("quarantine");
        setError(
          quarantined
            ? `This Practice desk preset is quarantined by an instructor (${err.message}). Pick another preset or ask an instructor to release it.`
            : `${err.message} [${err.code}]`,
        );
      } else {
        setError(err instanceof Error ? err.message : "Sandbox failed");
      }
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const initial = presetFromLocation();
    setPreset(initial);
    setFromLesson(fromLessonFromLocation());
    setFromMission(fromMissionFromLocation());
    void (async () => {
      try {
        const body = await listSandboxPresets();
        setPresets(body.presets);
      } catch {
        setPresets([
          { id: "site-ops-fixture", label: "Site Ops Practice desk" },
          { id: "support-fixture", label: "Support Practice desk" },
          { id: "ops-fixture", label: "Ops Practice desk" },
          { id: "eng-fixture", label: "Engineering Practice desk" },
        ]);
      }
      await ensureSandbox(initial);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onAddDoc() {
    if (!sandbox || !text.trim()) return;
    setBusy(true);
    try {
      const result = await addSandboxDocument(sandbox.id, { title, text });
      setSandbox(result.sandbox);
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Add document failed");
    } finally {
      setBusy(false);
    }
  }

  async function onRetrieve() {
    if (!sandbox) return;
    setBusy(true);
    try {
      const result = await retrieveSandbox(sandbox.id, { query, k: 3 });
      setHits(result.hits);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Retrieve failed");
    } finally {
      setBusy(false);
    }
  }

  async function onAddPart() {
    if (!sandbox) return;
    setBusy(true);
    try {
      const result = await addSandboxPart(sandbox.id, {
        part_number: partNumber,
        quantity: 1,
        incident_id: incidentId,
        note: "Academy practice entry",
      });
      setSandbox(result.sandbox);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Parts entry failed");
    } finally {
      setBusy(false);
    }
  }

  async function onReset() {
    if (!sandbox) return;
    setBusy(true);
    try {
      const result = await resetSandbox(sandbox.id);
      setSandbox(result.sandbox);
      setHits([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
    } finally {
      setBusy(false);
    }
  }

  async function onPresetChange(next: string) {
    setPreset(next);
    const url = new URL(window.location.href);
    url.searchParams.set("preset", next);
    window.history.replaceState({}, "", url.toString());
    setHits([]);
    await ensureSandbox(next);
  }

  return (
    <div className="academy-layout">
      <AcademySubnav />
      <header className="academy-lesson-hero academy-workspace-hero">
        <p className="empty-kicker academy-hero-kicker">Practice desk</p>
        <h1>Department sandboxes</h1>
        <p className="academy-lesson-summary">
          Principal-scoped RAG and parts ledger with curated teaching presets
          (Site Ops, Support, Ops, Eng). Isolated from shared Lab corpus. Never
          paste production secrets.
        </p>
        <div className="academy-hero-actions">
          {fromLesson ? (
            <Link className="btn" href={`/academy/lessons/${fromLesson}`}>
              Back to lesson
            </Link>
          ) : null}
          {fromMission ? (
            <Link className="btn" href={`/academy/missions/${fromMission}`}>
              Back to mission
            </Link>
          ) : null}
          <Link className="btn btn-ghost" href="/academy/workbench">
            Workbench
          </Link>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onReset}
            disabled={busy || !sandbox}
          >
            Reset sandbox
          </button>
        </div>
        <label className="academy-preset-pick">
          Preset
          <select
            value={preset}
            onChange={(e) => void onPresetChange(e.target.value)}
            disabled={busy}
          >
            {(presets.length
              ? presets
              : [{ id: preset, label: preset }]
            ).map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        {quotaHint ? <p className="muted academy-quota-meter">{quotaHint}</p> : null}
      </header>

      {error ? <div className="message-error models-error">{error}</div> : null}

      {!sandbox ? (
        <p className="muted">{busy ? "Opening sandbox…" : "No sandbox yet."}</p>
      ) : (
        <div className="academy-sandbox-grid">
          <section className="academy-panel">
            <h2>Documents ({sandbox.documents.length})</h2>
            <ul className="academy-doc-list">
              {sandbox.documents.map((doc) => (
                <li key={doc.id}>
                  <strong>{doc.title}</strong>
                  <span>{doc.text.slice(0, 120)}…</span>
                </li>
              ))}
            </ul>
            <label>
              Title
              <input value={title} onChange={(e) => setTitle(e.target.value)} />
            </label>
            <label>
              Paste text
              <textarea
                rows={5}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Paste a short sanitized note…"
              />
            </label>
            <button type="button" className="btn" onClick={onAddDoc} disabled={busy}>
              Add document
            </button>
          </section>

          <section className="academy-panel">
            <h2>Retrieve</h2>
            <div className="academy-sample-chips" aria-label="Sample queries">
              {samples.map((sample) => (
                <button
                  key={sample}
                  type="button"
                  className="academy-chip"
                  onClick={() => setQuery(sample)}
                >
                  {sample}
                </button>
              ))}
            </div>
            <label>
              Query
              <input value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <button type="button" className="btn" onClick={onRetrieve} disabled={busy}>
              Retrieve
            </button>
            <p className="muted">Runs used: {sandbox.retrieve_runs}</p>
            <ul className="academy-doc-list">
              {hits.map((hit) => (
                <li key={`${hit.title}-${hit.score}`}>
                  <strong>
                    {hit.title} ({hit.score})
                  </strong>
                  <span>{hit.snippet}</span>
                  <Link
                    className="btn btn-ghost"
                    href={`/academy/workbench?title=${encodeURIComponent(hit.title)}&body=${encodeURIComponent(hit.snippet)}`}
                  >
                    Save hit as note
                  </Link>
                </li>
              ))}
            </ul>
          </section>

          {showParts ? (
            <section className="academy-panel">
              <h2>Parts ledger</h2>
              <label>
                Part number
                <input value={partNumber} onChange={(e) => setPartNumber(e.target.value)} />
              </label>
              <label>
                Incident id
                <input value={incidentId} onChange={(e) => setIncidentId(e.target.value)} />
              </label>
              <button type="button" className="btn" onClick={onAddPart} disabled={busy}>
                Record part
              </button>
              <ul className="academy-doc-list">
                {sandbox.parts.map((part) => (
                  <li key={part.id}>
                    <strong>
                      {part.part_number} ×{part.quantity}
                    </strong>
                    <span>
                      {part.incident_id} — {part.note || "no note"}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <section className="academy-panel">
              <h2>Parts ledger</h2>
              <p className="muted">
                Site Ops–only teaching surface. Switch to{" "}
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => void onPresetChange("site-ops-fixture")}
                >
                  site-ops-fixture
                </button>{" "}
                to practice parts entries.
              </p>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
