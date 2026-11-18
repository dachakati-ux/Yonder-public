"use client";

import { useEffect, useState } from "react";
import { GATEWAY_URL } from "@/lib/brand";
import { fetchHardware } from "@/lib/ops";
import { listGpus } from "@/lib/hardware";
import type { HardwareNode } from "@/lib/hardware";

interface CatalogModel {
  id: string;
  url: string;
  downloads: number;
  likes: number;
  license: string | null;
  gated: boolean;
  private: boolean;
  pipeline_tag: string | null;
  has_gguf: boolean;
}

interface GgufFile {
  filename: string;
  size_bytes: number | null;
}

interface CatalogSearchResponse {
  models: CatalogModel[];
  error?: string;
}
interface DetailsResponse {
  model: CatalogModel;
  gguf_files: GgufFile[];
  error?: string;
  file_count_truncated?: boolean;
}

const bytes = (value: number | null) =>
  value == null ? "Size not reported" : `${(value / 1024 ** 3).toFixed(2)} GiB`;

export function ModelLibrary() {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<CatalogModel[]>([]);
  const [selected, setSelected] = useState<DetailsResponse | null>(null);
  const [working, setWorking] = useState(false);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [error, setError] = useState("");
  const [workers, setWorkers] = useState<HardwareNode[] | null>(null);

  useEffect(() => {
    let active = true;
    void fetchHardware().then((data) => {
      if (active) setWorkers(data.nodes.filter((n) => n.connected));
    }).catch(() => {
      if (active) setWorkers(null);
    });
    return () => { active = false; };
  }, []);

  async function search(value = query) {
    setWorking(true);
    setSelected(null);
    setError("");
    try {
      const params = new URLSearchParams({ q: value.trim(), limit: "12" });
      const response = await fetch(`${GATEWAY_URL}/api/admin/catalog/search?${params}`);
      if (!response.ok) throw new Error(`Catalog request failed (${response.status})`);
      const body = (await response.json()) as CatalogSearchResponse;
      if (body.error) throw new Error(body.error);
      setItems(body.models);
    } catch (err) {
      setItems([]);
      setError(err instanceof Error ? err.message : "Model search failed");
    } finally {
      setWorking(false);
    }
  }

  async function inspect(item: CatalogModel) {
    setDetailsLoading(true);
    setError("");
    try {
      const response = await fetch(`${GATEWAY_URL}/api/admin/catalog/models/${encodeURIComponent(item.id).replace("%2F", "/")}`);
      if (!response.ok) throw new Error(`Model details failed (${response.status})`);
      const body = (await response.json()) as DetailsResponse;
      if (body.error) throw new Error(body.error);
      setSelected(body);
    } catch (err) {
      setSelected(null);
      setError(err instanceof Error ? err.message : "Could not inspect model");
    } finally {
      setDetailsLoading(false);
    }
  }

  return (
    <section className="models-panel" aria-label="Hugging Face model library">
      <div className="models-panel-head">
        <div>
          <h2>Discover models</h2>
          <p className="entity-meta">
            Browse Hugging Face without leaving Yonder. Compare GGUF downloads before
            preparing a model for your worker.
          </p>
        </div>
      </div>
      <form
        onSubmit={(event) => { event.preventDefault(); void search(); }}
        style={{ display: "flex", flexWrap: "wrap", gap: 8, margin: "12px 0" }}
      >
        <input
          aria-label="Search Hugging Face models"
          placeholder="Search model names (e.g. Qwen, Gemma, Llama)"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          style={{ flex: "1 1 220px", minWidth: 0 }}
          maxLength={100}
        />
        <button className="btn btn-primary" disabled={working} type="submit">
          {working ? "Searching…" : "Search Hugging Face"}
        </button>
      </form>
      <p className="entity-meta">
        {workers == null
          ? "Hardware status unavailable — model compatibility cannot be verified."
          : `${workers.length} connected worker(s). Model sizes below are not a hardware-fit guarantee.`}
        {" "}Models may have license restrictions or require accepting provider terms.
      </p>
      {error ? <p className="message-error" role="alert">{error}</p> : null}
      {items.length > 0 ? (
        <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
          {items.map((item) => (
            <div key={item.id} className="compact-row" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
              <div style={{ flex: "1 1 240px", minWidth: 0 }}>
                <strong style={{ overflowWrap: "anywhere" }}>{item.id}</strong>
                <p className="entity-meta">
                  {item.pipeline_tag ?? "Task not reported"} · {item.downloads.toLocaleString()} downloads
                  {item.license ? ` · ${item.license}` : " · License not reported"}
                  {item.gated ? " · Access restricted" : ""}
                </p>
              </div>
              <button type="button" className="btn" disabled={detailsLoading} onClick={() => void inspect(item)}>
                Inspect GGUF
              </button>
              <a className="btn btn-ghost" href={item.url} target="_blank" rel="noopener noreferrer">
                Hugging Face ↗
              </a>
            </div>
          ))}
        </div>
      ) : !working && !error ? (
        <p className="entity-meta">Search for a model to see catalog results.</p>
      ) : null}
      {selected ? (
        <div className="models-panel" style={{ marginTop: 16 }}>
          <h3>{selected.model.id}</h3>
          <p className="entity-meta">
            Select a GGUF size to assess storage requirements. Downloading, licensing,
            worker compatibility and loading are not automated in this initial catalog release.
          </p>
          {selected.gguf_files.length === 0 ? (
            <p className="entity-meta">No GGUF files reported for this repository.</p>
          ) : (
            <div style={{ display: "grid", gap: 6, marginTop: 12 }}>
              {selected.gguf_files.map((file) => (
                <div key={file.filename} className="compact-row" style={{ display: "flex", gap: 12, justifyContent: "space-between", flexWrap: "wrap" }}>
                  <span style={{ overflowWrap: "anywhere" }}>{file.filename}</span>
                  <span style={{ display: "grid", justifyItems: "end", gap: 3 }}>
                    <strong>{bytes(file.size_bytes)}</strong>
                    {file.size_bytes != null && workers != null ? (
                      <small className="entity-meta">
                        {(() => {
                          const matching = workers.flatMap((node) =>
                            listGpus(node)
                              .filter((gpu) =>
                                gpu.availableBytes != null &&
                                gpu.availableBytes >= file.size_bytes! * 1.25
                              )
                              .map((gpu) => `${node.display_name}: ${gpu.name}`)
                          );
                          return matching.length > 0
                            ? `Potential single-GPU fit (25% headroom): ${matching.slice(0, 2).join(", ")}`
                            : "No reported single GPU has 25% weight headroom; CPU or multi-GPU may be possible";
                        })()}
                      </small>
                    ) : null}
                  </span>
                </div>
              ))}
            </div>
          )}
          {selected.file_count_truncated ? <p className="entity-meta">Showing the first 100 GGUF files.</p> : null}
          <p className="entity-meta">
            Weights are only part of the memory requirement. KV cache, context length,
            compute buffers and runtime compatibility must be checked before loading.
          </p>
        </div>
      ) : null}
    </section>
  );
}
