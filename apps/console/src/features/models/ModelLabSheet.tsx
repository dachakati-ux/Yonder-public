"use client";

import { useMemo, useState } from "react";
import metrics from "@/data/model-lab-metrics.json";

type PlacementCap = {
  model_id: string;
  display_name: string;
  family: string;
  single_max_ctx: number;
  two_max_ctx: number;
  evidence: string;
};

type ThroughputRow = {
  model_id: string;
  display_name: string;
  worker_id: string;
  mode: string;
  gpus: string;
  context_length: number;
  status: string;
  peak_used_mib: string | number;
  prompt_tokens: string | number;
  completion_tokens: string | number;
  ttft_ms: string | number;
  latency_ms: string | number;
  prefill_tok_s: string | number;
  decode_tok_s: string | number;
  token_source?: string;
  notes?: string;
};

type AbilityTask = {
  task_id: string;
  family: string;
  passed: boolean;
  latency_ms: number | string;
  reason: string;
};

type AbilityDoc = {
  model_id?: string;
  stamp?: string;
  passed?: number;
  total?: number;
  summary?: Record<string, { pass: number; fail: number }>;
  tasks?: AbilityTask[];
  coding_fixtures?: {
    stamp?: string;
    passed?: number;
    total?: number;
    method?: string;
    tasks?: AbilityTask[];
  };
};

type FillRow = {
  model_id: string;
  display_name: string;
  context_length: number;
  fill_pct: number;
  status: string;
  prompt_tokens: string | number;
  ttft_ms: string | number;
  prefill_tok_s: string | number;
  decode_tok_s: string | number;
  marker_ok?: boolean;
  notes?: string;
};

type FillDoc = {
  model_id?: string;
  stamp?: string;
  pcts?: number[];
  method?: string;
  rows?: FillRow[];
};

type FleetCodingTask = {
  task_id: string;
  passed?: boolean;
  kind?: string | null;
  reason?: string;
  family?: string;
  latency_ms?: number | string;
};

type FleetCodingModel = {
  model_id: string;
  display_name: string;
  passed?: number;
  total?: number;
  tasks?: FleetCodingTask[];
};

type FleetCodingDoc = {
  stamp?: string;
  worker?: string;
  method?: string;
  models?: FleetCodingModel[];
};

type MetricsDoc = {
  title: string;
  updated_at: string | null;
  source: string;
  method: string;
  placement_caps: PlacementCap[];
  throughput: ThroughputRow[];
  ability?: AbilityDoc;
  fill_throughput?: FillDoc;
  fleet_coding?: FleetCodingDoc;
};

const CONTEXTS = [32768, 65536, 131072, 262144] as const;
const FILL_PCTS = [10, 50, 85, 95] as const;

function fmt(value: string | number | null | undefined): string {
  if (value == null || value === "" || value === "not_reported") return "—";
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString()
      : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  const asNum = Number(value);
  if (value !== "" && Number.isFinite(asNum) && /^-?\d+(\.\d+)?$/.test(value)) {
    return asNum.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return String(value);
}

function ctxLabel(ctx: number): string {
  if (ctx >= 1024 && ctx % 1024 === 0) return `${ctx / 1024}K`;
  return ctx.toLocaleString();
}

export function ModelLabSheet() {
  const doc = metrics as MetricsDoc;
  const [tab, setTab] = useState<
    "matrix" | "caps" | "raw" | "ability" | "fill" | "coding"
  >("matrix");

  const byModel = useMemo(() => {
    const map = new Map<string, ThroughputRow[]>();
    for (const row of doc.throughput) {
      const list = map.get(row.model_id) ?? [];
      list.push(row);
      map.set(row.model_id, list);
    }
    return map;
  }, [doc.throughput]);

  const fillByCtx = useMemo(() => {
    const map = new Map<number, Map<number, FillRow>>();
    for (const row of doc.fill_throughput?.rows ?? []) {
      const inner = map.get(row.context_length) ?? new Map<number, FillRow>();
      inner.set(row.fill_pct, row);
      map.set(row.context_length, inner);
    }
    return map;
  }, [doc.fill_throughput]);

  const chatCaps = doc.placement_caps.filter((c) => c.family === "chat");
  const abilityTasks = [
    ...(doc.ability?.tasks ?? []),
    ...(doc.ability?.coding_fixtures?.tasks ?? []),
  ];

  return (
    <section className="models-panel model-lab-sheet">
      <details className="ops-collapsible" open={false}>
        <summary>
          <span className="ops-collapsible-title">Lab model data sheet</span>
          <span className="panel-count">
            {doc.throughput.length
              ? `${doc.throughput.length} throughput rows`
              : "caps ready · throughput pending"}
          </span>
        </summary>
        <div className="ops-collapsible-body model-lab-sheet-body">
          <p className="entity-meta model-lab-sheet-meta">
            {doc.title}
            {doc.updated_at ? ` · updated ${doc.updated_at}` : ""} · {doc.source}
          </p>
          <p className="entity-meta model-lab-sheet-meta">{doc.method}</p>

          <div className="model-lab-sheet-tabs" role="tablist">
            {(
              [
                ["matrix", "Throughput matrix"],
                ["fill", "Fill slowdown"],
                ["coding", "Coding fixtures"],
                ["caps", "Placement caps"],
                ["ability", "Ability"],
                ["raw", "Raw rows"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                className={
                  tab === id
                    ? "btn model-lab-sheet-tab is-active"
                    : "btn model-lab-sheet-tab"
                }
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "matrix" ? (
            <div className="sheet-scroll">
              <table className="sheet-table">
                <thead>
                  <tr>
                    <th className="sheet-sticky" rowSpan={2}>
                      Model
                    </th>
                    {CONTEXTS.map((ctx) => (
                      <th key={ctx} colSpan={2}>
                        {ctxLabel(ctx)} context
                      </th>
                    ))}
                  </tr>
                  <tr>
                    {CONTEXTS.map((ctx) => (
                      <FragmentPair key={ctx} />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {chatCaps.map((cap) => {
                    const rows = byModel.get(cap.model_id) ?? [];
                    return (
                      <tr key={cap.model_id}>
                        <th className="sheet-sticky sheet-rowhead" scope="row">
                          <span className="sheet-model-name">{cap.display_name}</span>
                          <span className="sheet-model-id">{cap.model_id}</span>
                        </th>
                        {CONTEXTS.map((ctx) => {
                          const row = rows.find((r) => r.context_length === ctx);
                          if (!row) {
                            const supported =
                              cap.single_max_ctx >= ctx || cap.two_max_ctx >= ctx;
                            return (
                              <FragmentCells
                                key={ctx}
                                decode={supported ? "…" : "n/a"}
                                prefill={supported ? "…" : "n/a"}
                                muted
                              />
                            );
                          }
                          if (row.status === "skipped") {
                            return (
                              <FragmentCells
                                key={ctx}
                                decode="n/a"
                                prefill="n/a"
                                muted
                              />
                            );
                          }
                          if (row.status !== "ok") {
                            return (
                              <FragmentCells
                                key={ctx}
                                decode="fail"
                                prefill="fail"
                                muted
                                title={row.notes || row.status}
                              />
                            );
                          }
                          const est =
                            row.token_source === "chars_div_4_estimate" ? " est." : "";
                          return (
                            <FragmentCells
                              key={ctx}
                              decode={`${fmt(row.decode_tok_s)}${est}`}
                              prefill={`${fmt(row.prefill_tok_s)}${est}`}
                              title={`${row.mode} · ${row.status} · peak ${fmt(row.peak_used_mib)} MiB · ${row.token_source || ""}`}
                            />
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!doc.throughput.length ? (
                <p className="entity-meta" style={{ marginTop: "0.75rem" }}>
                  Throughput cells fill in after the lab bench finishes. Placement
                  caps below are already measured.
                </p>
              ) : null}
            </div>
          ) : null}

          {tab === "fill" ? (
            <div className="sheet-scroll">
              {doc.fill_throughput?.rows?.length ? (
                <>
                  <p className="entity-meta model-lab-sheet-meta">
                    {doc.fill_throughput.model_id || "model"} fill-ratio decode
                    {doc.fill_throughput.stamp
                      ? ` · ${doc.fill_throughput.stamp}`
                      : ""}
                  </p>
                  <p className="entity-meta model-lab-sheet-meta">
                    {doc.fill_throughput.method}
                  </p>
                  <table className="sheet-table">
                    <thead>
                      <tr>
                        <th className="sheet-sticky" rowSpan={2}>
                          Context
                        </th>
                        {FILL_PCTS.map((pct) => (
                          <th key={pct} colSpan={2}>
                            {pct}% full
                          </th>
                        ))}
                      </tr>
                      <tr>
                        {FILL_PCTS.map((pct) => (
                          <FragmentPair key={pct} />
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {CONTEXTS.map((ctx) => {
                        const byPct = fillByCtx.get(ctx);
                        return (
                          <tr key={ctx}>
                            <th
                              className="sheet-sticky sheet-rowhead"
                              scope="row"
                            >
                              {ctxLabel(ctx)}
                            </th>
                            {FILL_PCTS.map((pct) => {
                              const row = byPct?.get(pct);
                              if (!row) {
                                return (
                                  <FragmentCells
                                    key={pct}
                                    decode="…"
                                    prefill="…"
                                    muted
                                  />
                                );
                              }
                              if (row.status !== "ok") {
                                return (
                                  <FragmentCells
                                    key={pct}
                                    decode="fail"
                                    prefill="fail"
                                    muted
                                    title={row.notes || row.status}
                                  />
                                );
                              }
                              return (
                                <FragmentCells
                                  key={pct}
                                  decode={fmt(row.decode_tok_s)}
                                  prefill={fmt(row.prefill_tok_s)}
                                  title={`ttft ${fmt(row.ttft_ms)} ms · prompt ${fmt(row.prompt_tokens)}`}
                                />
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </>
              ) : (
                <p className="entity-meta">No fill-ratio rows yet.</p>
              )}
            </div>
          ) : null}

          {tab === "coding" ? (
            <div className="sheet-scroll">
              {doc.fleet_coding?.models?.length ? (
                <>
                  <p className="entity-meta model-lab-sheet-meta">
                    Fleet coding fixtures
                    {doc.fleet_coding.stamp
                      ? ` · ${doc.fleet_coding.stamp}`
                      : ""}
                    {doc.fleet_coding.worker
                      ? ` · ${doc.fleet_coding.worker}`
                      : ""}
                  </p>
                  <p className="entity-meta model-lab-sheet-meta">
                    {doc.fleet_coding.method}
                  </p>
                  <table className="sheet-table">
                    <thead>
                      <tr>
                        <th>Model</th>
                        <th>Pass</th>
                        <th>Total</th>
                        <th>Failed tasks</th>
                      </tr>
                    </thead>
                    <tbody>
                      {doc.fleet_coding.models.map((m) => {
                        const failed = (m.tasks ?? [])
                          .filter((t) => !t.passed)
                          .map((t) => t.task_id)
                          .join(", ");
                        return (
                          <tr key={m.model_id}>
                            <td>
                              <span className="sheet-model-name">
                                {m.display_name}
                              </span>
                              <span className="sheet-model-id">{m.model_id}</span>
                            </td>
                            <td className="sheet-num">{fmt(m.passed)}</td>
                            <td className="sheet-num">{fmt(m.total)}</td>
                            <td>{failed || "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </>
              ) : (
                <p className="entity-meta">No fleet coding fixture rows yet.</p>
              )}
            </div>
          ) : null}

          {tab === "caps" ? (
            <div className="sheet-scroll">
              <table className="sheet-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Family</th>
                    <th>1× max ctx</th>
                    <th>2× max ctx</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.placement_caps.map((cap) => (
                    <tr key={cap.model_id}>
                      <td>{cap.display_name}</td>
                      <td>{cap.family}</td>
                      <td className="sheet-num">
                        {cap.single_max_ctx === 0
                          ? "blocked"
                          : fmt(cap.single_max_ctx)}
                      </td>
                      <td className="sheet-num">{fmt(cap.two_max_ctx)}</td>
                      <td>{cap.evidence}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {tab === "ability" ? (
            <div className="sheet-scroll">
              {abilityTasks.length ? (
                <>
                  <p className="entity-meta model-lab-sheet-meta">
                    {doc.ability?.model_id || "model"} · ability{" "}
                    {doc.ability?.passed}/{doc.ability?.total}
                    {doc.ability?.coding_fixtures
                      ? ` · fixtures ${doc.ability.coding_fixtures.passed}/${doc.ability.coding_fixtures.total}`
                      : ""}
                    {doc.ability?.stamp ? ` · ${doc.ability.stamp}` : ""}
                    {doc.ability?.summary
                      ? ` · ${Object.entries(doc.ability.summary)
                          .map(
                            ([fam, s]) =>
                              `${fam} ${s.pass}/${s.pass + s.fail}`,
                          )
                          .join(", ")}`
                      : ""}
                  </p>
                  <table className="sheet-table">
                    <thead>
                      <tr>
                        <th>Task</th>
                        <th>Family</th>
                        <th>Result</th>
                        <th>Latency ms</th>
                        <th>Notes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {abilityTasks.map((task) => (
                        <tr key={`${task.family}-${task.task_id}`}>
                          <td>{task.task_id}</td>
                          <td>{task.family}</td>
                          <td>{task.passed ? "pass" : "fail"}</td>
                          <td className="sheet-num">{fmt(task.latency_ms)}</td>
                          <td>{task.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              ) : (
                <p className="entity-meta">No ability battery rows yet.</p>
              )}
            </div>
          ) : null}

          {tab === "raw" ? (
            <div className="sheet-scroll">
              <table className="sheet-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Ctx</th>
                    <th>Mode</th>
                    <th>Status</th>
                    <th>Peak MiB</th>
                    <th>Prefill t/s</th>
                    <th>Decode t/s</th>
                    <th>TTFT ms</th>
                    <th>Prompt tok</th>
                    <th>Compl tok</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.throughput.length ? (
                    doc.throughput.map((row, idx) => (
                      <tr key={`${row.model_id}-${row.context_length}-${idx}`}>
                        <td>{row.display_name}</td>
                        <td className="sheet-num">{fmt(row.context_length)}</td>
                        <td>{row.mode}</td>
                        <td>{row.status}</td>
                        <td className="sheet-num">{fmt(row.peak_used_mib)}</td>
                        <td className="sheet-num">{fmt(row.prefill_tok_s)}</td>
                        <td className="sheet-num">{fmt(row.decode_tok_s)}</td>
                        <td className="sheet-num">{fmt(row.ttft_ms)}</td>
                        <td className="sheet-num">{fmt(row.prompt_tokens)}</td>
                        <td className="sheet-num">
                          {fmt(row.completion_tokens)}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={10} className="sheet-empty">
                        No throughput rows yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      </details>
    </section>
  );
}

function FragmentPair() {
  return (
    <>
      <th className="sheet-subhead">Decode t/s</th>
      <th className="sheet-subhead">Prefill t/s</th>
    </>
  );
}

function FragmentCells({
  decode,
  prefill,
  muted,
  title,
}: {
  decode: string;
  prefill: string;
  muted?: boolean;
  title?: string;
}) {
  return (
    <>
      <td
        className={muted ? "sheet-num sheet-muted" : "sheet-num"}
        title={title}
      >
        {decode}
      </td>
      <td
        className={muted ? "sheet-num sheet-muted" : "sheet-num"}
        title={title}
      >
        {prefill}
      </td>
    </>
  );
}
