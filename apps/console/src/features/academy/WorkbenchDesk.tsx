"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  GatewayError,
  createWorkbenchNote,
  deleteWorkbenchNote,
  exportWorkbench,
  listWorkbenchNotes,
  patchWorkbenchNote,
  type WorkbenchNote,
} from "@/lib/gateway";
import { AcademySubnav } from "./AcademySubnav";
import {
  buildEvidencePack,
  downloadEvidencePack,
} from "./evidencePack";

export function WorkbenchDesk() {
  const [notes, setNotes] = useState<WorkbenchNote[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [exportJson, setExportJson] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [packBusy, setPackBusy] = useState(false);

  async function load() {
    try {
      const result = await listWorkbenchNotes();
      setNotes(result.notes);
      setError(null);
    } catch (err) {
      setError(
        err instanceof GatewayError
          ? `${err.message} [${err.code}]`
          : err instanceof Error
            ? err.message
            : "Failed to load workbench",
      );
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return notes;
    return notes.filter(
      (n) =>
        n.title.toLowerCase().includes(q) ||
        n.body.toLowerCase().includes(q) ||
        n.tags.some((t) => t.toLowerCase().includes(q)),
    );
  }, [notes, filter]);

  async function onCreate() {
    if (!title.trim() || !body.trim()) return;
    setBusy(true);
    try {
      if (editingId) {
        await patchWorkbenchNote(editingId, {
          title: title.trim(),
          body,
          tags: tags
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean),
        });
        setEditingId(null);
      } else {
        await createWorkbenchNote({
          title: title.trim(),
          body,
          tags: tags
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean),
          source: "manual",
        });
      }
      setTitle("");
      setBody("");
      setTags("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  function onEdit(note: WorkbenchNote) {
    setEditingId(note.id);
    setTitle(note.title);
    setBody(note.body);
    setTags(note.tags.join(", "));
  }

  async function onDelete(noteId: string) {
    setBusy(true);
    try {
      await deleteWorkbenchNote(noteId);
      if (editingId === noteId) {
        setEditingId(null);
        setTitle("");
        setBody("");
        setTags("");
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  async function onExport() {
    setBusy(true);
    try {
      const payload = await exportWorkbench();
      const text = JSON.stringify(payload, null, 2);
      setExportJson(text);
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `yonder-workbench-${payload.principal_subject || "export"}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  async function onEvidencePack() {
    setPackBusy(true);
    try {
      const pack = await buildEvidencePack();
      downloadEvidencePack(pack);
      setExportJson(JSON.stringify(pack, null, 2));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Evidence pack failed");
    } finally {
      setPackBusy(false);
    }
  }

  return (
    <div className="academy-layout">
      <AcademySubnav />
      <header className="academy-lesson-hero academy-workspace-hero">
        <p className="empty-kicker academy-hero-kicker">Workbench</p>
        <h1>Personal notes</h1>
        <p className="academy-lesson-summary">
          Principal-scoped scratchpad for mission evidence, tool outputs, and
          export. Not shared across identities.
        </p>
        <div className="academy-hero-actions">
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => void onExport()}
          >
            Download notes
          </button>
          <button
            type="button"
            className="btn"
            disabled={packBusy}
            onClick={() => void onEvidencePack()}
          >
            {packBusy ? "Building…" : "Download evidence pack"}
          </button>
          <Link className="btn btn-ghost" href="/academy/missions/siteops-incident-loop">
            Site Ops live desk
          </Link>
        </div>
      </header>

      {error ? <div className="message-error models-error">{error}</div> : null}

      <section className="academy-sandbox-grid">
        <div className="academy-panel">
          <h2>{editingId ? "Edit note" : "New note"}</h2>
          <label>
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            Tags (comma-separated)
            <input value={tags} onChange={(e) => setTags(e.target.value)} />
          </label>
          <label>
            Body
            <textarea
              rows={10}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Paste FIX-1001 tool output, runbook titles, citations…"
            />
          </label>
          <div className="academy-hero-actions">
            <button
              type="button"
              className="btn"
              disabled={busy || !title.trim() || !body.trim()}
              onClick={() => void onCreate()}
            >
              {editingId ? "Update note" : "Save note"}
            </button>
            {editingId ? (
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => {
                  setEditingId(null);
                  setTitle("");
                  setBody("");
                  setTags("");
                }}
              >
                Cancel edit
              </button>
            ) : null}
          </div>
        </div>
        <div className="academy-panel">
          <h2>Your notes ({filtered.length})</h2>
          <label>
            Filter
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search title, tags, body…"
            />
          </label>
          {filtered.length === 0 ? (
            <p className="muted">No notes yet.</p>
          ) : (
            <ul className="academy-note-list">
              {filtered.map((note) => (
                <li key={note.id}>
                  <div>
                    <strong>{note.title}</strong>
                    <p className="muted">
                      {note.tags.join(", ") || "untagged"} · {note.updated_at}
                    </p>
                    <pre className="academy-note-body">{note.body}</pre>
                  </div>
                  <div className="academy-hero-actions">
                    <button
                      type="button"
                      className="btn btn-ghost"
                      disabled={busy}
                      onClick={() => onEdit(note)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost"
                      disabled={busy}
                      onClick={() => void onDelete(note.id)}
                    >
                      Delete
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {exportJson ? (
        <section className="academy-panel">
          <h2>Last export preview</h2>
          <pre className="academy-note-body">{exportJson}</pre>
        </section>
      ) : null}
    </div>
  );
}
