"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  CODER_ALIAS,
  buildCoderChatHref,
  coderSessionTrustChips,
} from "@/features/coder/coderContract";
import {
  GatewayError,
  coderAdvanceRun,
  coderCancelRun,
  coderCloneWorkspace,
  coderCreateRun,
  coderCreateWorkspace,
  coderGrantWorkspace,
  coderListDir,
  coderListRuns,
  coderListWorkspaces,
  coderReadFile,
  coderRevokeWorkspace,
  coderSearch,
  coderStatus,
  getCapacitySnapshot,
  type CoderRun,
  type CoderStatus,
  type CoderWorkspace,
} from "@/lib/gateway";

export function CoderDesk() {
  const [status, setStatus] = useState<CoderStatus | null>(null);
  const [aliasAvailability, setAliasAvailability] = useState<string>("not_reported");
  const [workspaces, setWorkspaces] = useState<CoderWorkspace[]>([]);
  const [runs, setRuns] = useState<CoderRun[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [activeRunId, setActiveRunId] = useState<string>("");
  const [grantPath, setGrantPath] = useState("");
  const [clonePath, setClonePath] = useState("");
  const [runTask, setRunTask] = useState("Fix the BUG so tests pass.");
  const [toolPath, setToolPath] = useState(".");
  const [toolQuery, setToolQuery] = useState("");
  const [toolOut, setToolOut] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [coder, capacity, listed, runList] = await Promise.all([
        coderStatus(),
        getCapacitySnapshot(),
        coderListWorkspaces().catch(() => ({ workspaces: [] as CoderWorkspace[] })),
        coderListRuns().catch(() => ({ runs: [] as CoderRun[] })),
      ]);
      setStatus(coder);
      const row = capacity.aliases.find((a) => a.alias === CODER_ALIAS);
      setAliasAvailability(row?.availability ?? "not_reported");
      setWorkspaces(listed.workspaces);
      setRuns(runList.runs);
      if (!activeId && listed.workspaces[0]) {
        setActiveId(listed.workspaces[0].id);
      }
      if (!activeRunId && runList.runs[0]) {
        setActiveRunId(runList.runs[0].id);
      }
    } catch (err) {
      const message =
        err instanceof GatewayError
          ? `${err.message} (${err.code})`
          : err instanceof Error
            ? err.message
            : "Failed to reach Gateway coder APIs";
      setError(message);
    }
  }, [activeId, activeRunId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll while a run is executing in the background so the timeline, metrics,
  // and verdicts update live without a manual refresh.
  const runActive = runs.some((r) => r.status === "active");
  useEffect(() => {
    if (!runActive) {
      return;
    }
    const timer = window.setInterval(() => {
      void refresh();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [runActive, refresh]);

  const chips = coderSessionTrustChips({
    aliasAvailability,
    hasWorkspace: workspaces.length > 0,
  });

  const activeRun = runs.find((r) => r.id === activeRunId) ?? runs[0] ?? null;

  async function onCreateCage() {
    setBusy(true);
    setError(null);
    try {
      const created = await coderCreateWorkspace();
      setActiveId(created.workspace.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create cage failed");
    } finally {
      setBusy(false);
    }
  }

  async function onGrant() {
    setBusy(true);
    setError(null);
    try {
      const created = await coderGrantWorkspace(grantPath.trim());
      setActiveId(created.workspace.id);
      setGrantPath("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Grant failed");
    } finally {
      setBusy(false);
    }
  }

  async function onClone() {
    setBusy(true);
    setError(null);
    try {
      const created = await coderCloneWorkspace(clonePath.trim());
      setActiveId(created.workspace.id);
      setClonePath("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Clone failed");
    } finally {
      setBusy(false);
    }
  }

  async function onRevoke(id: string) {
    setBusy(true);
    setError(null);
    try {
      await coderRevokeWorkspace(id);
      if (activeId === id) setActiveId("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Revoke failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCreateRun() {
    if (!activeId) {
      setError("Select a workspace first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await coderCreateRun(activeId, runTask.trim());
      setActiveRunId(created.run.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create run failed");
    } finally {
      setBusy(false);
    }
  }

  async function onAdvance(auto: boolean) {
    if (!activeRunId) {
      setError("Select or create a run first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const advanced = await coderAdvanceRun(activeRunId, auto);
      setActiveRunId(advanced.run.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Advance failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCancel() {
    if (!activeRunId) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await coderCancelRun(activeRunId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancel failed");
    } finally {
      setBusy(false);
    }
  }

  async function runTool(kind: "list" | "read" | "search") {
    if (!activeId) {
      setError("Select or create a workspace first.");
      return;
    }
    setBusy(true);
    setError(null);
    setToolOut(null);
    try {
      if (kind === "list") {
        const out = await coderListDir(activeId, toolPath);
        setToolOut(JSON.stringify(out, null, 2));
      } else if (kind === "read") {
        const out = await coderReadFile(activeId, toolPath);
        setToolOut(JSON.stringify(out, null, 2));
      } else {
        const out = await coderSearch(activeId, toolQuery || "TODO", toolPath);
        setToolOut(JSON.stringify(out, null, 2));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Tool call failed");
    } finally {
      setBusy(false);
    }
  }

  const sessionHref = buildCoderChatHref({
    prompt: "Review the pasted code. Propose a unified diff only if needed.",
    hasWorkspace: workspaces.length > 0,
  });

  return (
    <div className="lab-layout">
      <header className="academy-lesson-hero academy-workspace-hero">
        <p className="empty-kicker academy-hero-kicker">Coder · Pipeline</p>
        <h1>Internal Coder</h1>
        <p className="academy-lesson-summary">
          Local Gateway-only coding pipeline. Link a local repo, run staged
          explore → plan → implement → check → dual-agent verify → push. Write
          and push stay locked until <code>write_unlock</code> is proven. Forgejo
          comes later.
        </p>
        <div className="academy-hero-actions">
          <Link className="btn" href={sessionHref}>
            Open coder chat
          </Link>
          <Link className="btn btn-ghost" href="/academy/capacity">
            Capacity
          </Link>
          <Link className="btn btn-ghost" href="/help#coder-assistant">
            Glossary
          </Link>
        </div>
      </header>

      <div className="academy-criteria-chips" aria-label="Coder trust chips">
        {chips.map((chip) => (
          <span
            key={chip.id}
            className={
              chip.kind === "ok"
                ? "academy-chip academy-chip-met"
                : chip.kind === "warn"
                  ? "academy-chip academy-chip-missed"
                  : "academy-chip"
            }
          >
            {chip.label}
          </span>
        ))}
        {status?.write_unlock ? (
          <span className="academy-chip academy-chip-missed">write_unlock</span>
        ) : (
          <span className="academy-chip academy-chip-met">write_locked</span>
        )}
      </div>

      {error ? (
        <p className="lab-banner" role="alert">
          {error}
        </p>
      ) : null}

      <section className="academy-panel">
        <h2>Lane status</h2>
        <p className="muted">
          Alias availability comes from Gateway capacity — never invented as ready.
        </p>
        <ul className="academy-objectives">
          <li>
            Alias: <code>{CODER_ALIAS}</code>
          </li>
          <li>
            Availability: <code>{aliasAvailability}</code>
          </li>
          <li>
            Coder API:{" "}
            <code>
              {status
                ? status.enabled
                  ? `enabled · write_unlock=${status.write_unlock} · agent=${status.agent_mode ?? "not_reported"}`
                  : "disabled"
                : "not_reported"}
            </code>
          </li>
          <li>
            Phase: <code>{status?.phase ?? "not_reported"}</code>
          </li>
        </ul>
      </section>

      <section className="academy-panel">
        <h2>Workspaces</h2>
        <p className="muted">
          Cage, grant, or local clone under grantable roots. Path jail rejects
          traversal and secret-like names.
        </p>
        <div className="academy-hero-actions">
          <button type="button" className="btn" disabled={busy} onClick={onCreateCage}>
            Create cage workspace
          </button>
        </div>
        <div className="composer" style={{ marginTop: "1rem" }}>
          <label>
            Grant human-directed path
            <input
              value={grantPath}
              onChange={(e) => setGrantPath(e.target.value)}
              placeholder="/path/under/grantable/root"
              disabled={busy}
            />
          </label>
          <button type="button" className="btn" disabled={busy || !grantPath.trim()} onClick={onGrant}>
            Grant path
          </button>
        </div>
        <div className="composer" style={{ marginTop: "1rem" }}>
          <label>
            Link local repo (clone into cage)
            <input
              value={clonePath}
              onChange={(e) => setClonePath(e.target.value)}
              placeholder="/path/to/local/repo"
              disabled={busy}
            />
          </label>
          <button
            type="button"
            className="btn"
            disabled={busy || !clonePath.trim()}
            onClick={onClone}
          >
            Clone local
          </button>
        </div>
        {workspaces.length === 0 ? (
          <p className="muted">No workspaces for this principal yet.</p>
        ) : (
          <ul className="academy-objectives">
            {workspaces.map((ws) => (
              <li key={ws.id}>
                <label>
                  <input
                    type="radio"
                    name="coder-ws"
                    checked={activeId === ws.id}
                    onChange={() => setActiveId(ws.id)}
                  />{" "}
                  <code>{ws.id}</code> · {ws.kind} · <code>{ws.root_display}</code>
                </label>{" "}
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() => onRevoke(ws.id)}
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="academy-panel">
        <h2>Pipeline runs</h2>
        <p className="muted">
          Stages: intake → explore → plan → implement → check → dual_verify →
          push. The agent runs in the background (live model calls); failed
          checks feed back into a fix loop. Push requires two independent
          approve verdicts and passing check evidence. Blocked while{" "}
          <code>write_unlock=false</code>.
        </p>
        <div className="composer">
          <label>
            Task
            <input
              value={runTask}
              onChange={(e) => setRunTask(e.target.value)}
              disabled={busy}
            />
          </label>
        </div>
        <div className="academy-hero-actions">
          <button type="button" className="btn" disabled={busy} onClick={onCreateRun}>
            Start run
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy || !activeRunId}
            onClick={() => onAdvance(false)}
          >
            Run in background
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy || !activeRunId || activeRun?.status !== "active"}
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>
        {runs.length === 0 ? (
          <p className="muted">No runs yet.</p>
        ) : (
          <ul className="academy-objectives">
            {runs.map((run) => (
              <li key={run.id}>
                <label>
                  <input
                    type="radio"
                    name="coder-run"
                    checked={activeRunId === run.id}
                    onChange={() => setActiveRunId(run.id)}
                  />{" "}
                  <code>{run.id}</code> · {run.stage} · {run.status}
                  {run.dual_approved ? " · dual_approved" : ""}
                </label>
              </li>
            ))}
          </ul>
        )}
        {activeRun ? (
          <div style={{ marginTop: "1rem" }}>
            <h3>Timeline · {activeRun.id}</h3>
            <ul className="academy-objectives">
              {(activeRun.timeline ?? []).map((ev, idx) => (
                <li key={`${ev.stage}-${idx}`}>
                  <code>{ev.stage}</code> · {ev.result} — {ev.message}
                </li>
              ))}
            </ul>
            {activeRun.verdicts && activeRun.verdicts.length > 0 ? (
              <>
                <h3>Dual-agent verdicts</h3>
                <ul className="academy-objectives">
                  {activeRun.verdicts.map((v, idx) => (
                    <li key={`${v.agent ?? "agent"}-${idx}`}>
                      Agent {v.agent ?? "?"}: <code>{v.verdict}</code>
                      {v.reasons?.length ? ` — ${v.reasons.join("; ")}` : ""}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {activeRun.push_result ? (
              <p className="muted">
                Push: <code>{JSON.stringify(activeRun.push_result)}</code>
              </p>
            ) : null}
            {activeRun.error ? (
              <p className="lab-banner" role="status">
                {activeRun.error}
              </p>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="academy-panel">
        <h2>Read tools (proof)</h2>
        <p className="muted">
          Exercise jailed list / read / search against the selected workspace.
        </p>
        <div className="composer">
          <label>
            Relative path
            <input
              value={toolPath}
              onChange={(e) => setToolPath(e.target.value)}
              disabled={busy}
            />
          </label>
          <label>
            Search query
            <input
              value={toolQuery}
              onChange={(e) => setToolQuery(e.target.value)}
              disabled={busy}
              placeholder="TODO"
            />
          </label>
        </div>
        <div className="academy-hero-actions">
          <button type="button" className="btn" disabled={busy} onClick={() => runTool("list")}>
            List
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => runTool("read")}>
            Read
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => runTool("search")}>
            Search
          </button>
        </div>
        {toolOut ? (
          <pre className="coder-tool-out" tabIndex={0}>
            {toolOut}
          </pre>
        ) : null}
      </section>
    </div>
  );
}
