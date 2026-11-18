"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Gauge, StatTile, TonePill } from "@/components/metrics";
import { LoadProgressBar } from "@/components/LoadProgressBar";
import { GatewayError } from "@/lib/gateway";
import { NOT_REPORTED, formatBytes, formatCount } from "@/lib/format";
import {
  type AliasView,
  type DeploymentView,
  type ModelFolderCandidate,
  type ModelDefinition,
  drainDeployment,
  fetchDeploymentLogs,
  fetchModelRoots,
  fetchModelsAdmin,
  fetchRoutes,
  loadDeployment,
  repointAlias,
  prepareModelFolder,
  retryDeployment,
  runEmbeddingTest,
  runGenerationTest,
  runRerankTest,
  runTestPrompt,
  scanModelFolder,
  unloadDeployment,
  setDeploymentLock,
  fetchCapacityEvents,
  validateModel,
} from "@/lib/admin";
import { type GpuView, type StatusTone, listGpus, summarizeNode } from "@/lib/hardware";
import {
  type PlacementCheck,
  type PlacementMode,
  allowedContextPresets,
  evaluatePlacement,
  isGenerationModel,
  isSchedulable,
  minimumGpuMemoryBytes,
  modeMaxContextLength,
  modelProvider,
  occupantsForGpus,
  partitionModelDefinitions,
  resolveContextSelection,
} from "@/lib/placement";
import { fetchHardware, type HardwareNode } from "@/lib/ops";
import { ModelLabSheet } from "@/features/models/ModelLabSheet";

const READY_STATUSES = ["ready", "loaded"];

function statusClass(status: string): string {
  return `status-pill status-${status}`;
}

function deploymentTone(status: string): StatusTone {
  if (READY_STATUSES.includes(status)) return "ok";
  if (["failed", "error"].includes(status)) return "danger";
  if (["loading", "queued", "validating", "draining", "pending"].includes(status)) {
    return "warn";
  }
  return "neutral";
}

const IN_FLIGHT_LOAD_STATUSES = new Set(["queued", "loading", "validating"]);

function isInFlightLoad(status: string): boolean {
  return IN_FLIGHT_LOAD_STATUSES.has(status);
}

function checkTone(level: PlacementCheck["level"]): StatusTone {
  if (level === "ok") return "ok";
  if (level === "warn") return "warn";
  return "danger";
}

function GpuTile({
  gpu,
  selected,
  pooled,
  occupant,
  busy,
  onSelect,
  onUnload,
}: {
  gpu: GpuView;
  selected: boolean;
  pooled: boolean;
  occupant: DeploymentView | null;
  busy: boolean;
  onSelect: () => void;
  onUnload: (deploymentId: string) => void;
}) {
  return (
    <div
      className="gpu-tile"
      data-selected={selected}
      data-pool={pooled}
      data-occupied={Boolean(occupant)}
      data-disabled={busy}
      role="button"
      tabIndex={busy ? -1 : 0}
      aria-pressed={selected}
      aria-label={`Select GPU ${gpu.index}${gpu.name ? `, ${gpu.name}` : ""}`}
      onClick={() => {
        if (!busy) onSelect();
      }}
      onKeyDown={(event) => {
        if (busy) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="gpu-tile-head">
        <div className="gpu-tile-select">
          <span className="gpu-tile-name">
            GPU {gpu.index}
            {gpu.indexReported ? "" : " (index not_reported)"}
          </span>
          <span className="gpu-tile-model">{gpu.name}</span>
        </div>
        <TonePill tone={occupant ? "warn" : pooled ? "warn" : "ok"}>
          {occupant ? "occupied" : pooled ? "pool" : "free"}
        </TonePill>
      </div>
      <Gauge label="VRAM" percent={gpu.memoryPercent} detail={gpu.memoryText} />
      <div className="gpu-tile-foot">
        {occupant ? (
          <>
            <span className="entity-meta">
              {occupant.id} ({occupant.status})
            </span>
            <button
              type="button"
              className="btn btn-danger"
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                onUnload(occupant.id);
              }}
            >
              Unload
            </button>
          </>
        ) : (
          <span className="entity-meta">
            {gpu.availableBytes != null
              ? `available for placement · ${formatBytes(gpu.availableBytes)} free`
              : `no Yonder occupant · free VRAM ${NOT_REPORTED}`}
          </span>
        )}
      </div>
    </div>
  );
}

function DefinitionRow({
  model,
  busy,
  onValidate,
  onSelectForPlacement,
  onMockLoad,
}: {
  model: ModelDefinition;
  busy: boolean;
  onValidate: () => void;
  onSelectForPlacement: () => void;
  onMockLoad: () => void;
}) {
  const runtime = model.manifest.runtime as Record<string, unknown> | undefined;
  const requirements = model.manifest.requirements as
    | Record<string, unknown>
    | undefined;
  const schedulable = isSchedulable(model);
  const provider = String(runtime?.provider ?? "");
  return (
    <details className="compact-row">
      <summary>
        <span className="compact-row-title">{model.display_name}</span>
        <TonePill tone={schedulable ? "ok" : "warn"}>
          {schedulable ? "schedulable" : "unverified"}
        </TonePill>
      </summary>
      <div className="compact-row-body">
        <p className="entity-meta">id: {model.id}</p>
        <p className="entity-meta">
          runtime: {provider || "—"} / {String(runtime?.model_identifier ?? "—")}
        </p>
        <p className="entity-meta">
          format/quant: {String(runtime?.dtype ?? "—")} /{" "}
          {String(runtime?.quantization ?? "—")}
        </p>
        <p className="entity-meta">
          context: {String(runtime?.context_length ?? "—")} · GPU mem ≥{" "}
          {String(requirements?.minimum_gpu_memory_gb ?? "—")} GB
        </p>
        <p className="entity-meta">
          validation: {model.validation_status}
          {schedulable ? "" : " · inventory/unverified (not schedulable)"}
        </p>
        <div className="entity-actions">
          <button
            type="button"
            className="btn btn-ghost"
            disabled={busy}
            onClick={onValidate}
          >
            Validate
          </button>
          {schedulable ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => {
                if (provider !== "mock") {
                  onSelectForPlacement();
                  return;
                }
                onMockLoad();
              }}
            >
              {provider !== "mock" ? "Select for placement" : "Load"}
            </button>
          ) : (
            <span className="entity-meta">Load gated (unverified)</span>
          )}
        </div>
      </div>
    </details>
  );
}

export function ModelsWorkspace() {
  const [models, setModels] = useState<ModelDefinition[]>([]);
  const [deployments, setDeployments] = useState<DeploymentView[]>([]);
  const [aliases, setAliases] = useState<AliasView[]>([]);
  const [hardwareNodes, setHardwareNodes] = useState<HardwareNode[]>([]);
  const [selectedDeployment, setSelectedDeployment] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{
    message: string;
    conflictingDeploymentIds: string[];
    contestedGpuIndexes: number[];
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [testOutput, setTestOutput] = useState<string | null>(null);
  const [modelPath, setModelPath] = useState("");
  const [modelRoots, setModelRoots] = useState<Array<{ path: string; exists: boolean }>>([]);
  const [discovered, setDiscovered] = useState<ModelFolderCandidate[]>([]);

  const [loadModelId, setLoadModelId] = useState("");
  const [loadWorkerId, setLoadWorkerId] = useState("");
  const [loadContextLength, setLoadContextLength] = useState<number | null>(null);
  const [selectedGpus, setSelectedGpus] = useState<number[]>([]);
  const [openNodes, setOpenNodes] = useState<Set<string>>(new Set());
  const [placementLoading, setPlacementLoading] = useState(false);
  const [loadStartedAt, setLoadStartedAt] = useState<number | null>(null);
  const [loadElapsedSec, setLoadElapsedSec] = useState(0);
  const [capacityBanner, setCapacityBanner] = useState<string | null>(null);
  const [capacityBannerDismissed, setCapacityBannerDismissed] = useState(false);

  const refresh = useCallback(async () => {
    const [modelRows, routeData, hardware, capacity] = await Promise.all([
      fetchModelsAdmin(),
      fetchRoutes(),
      fetchHardware().catch(() => ({ nodes: [] as HardwareNode[] })),
      fetchCapacityEvents(12).catch(() => null),
    ]);
    setModels(modelRows);
    setDeployments(routeData.deployments);
    setAliases(routeData.aliases);
    setHardwareNodes(hardware.nodes);
    if (capacity?.events?.length) {
      const notice = capacity.events.find((event) =>
        ["demote", "evict"].includes(event.event_type),
      );
      if (notice && !capacityBannerDismissed) {
        const detail =
          typeof notice.details?.from_model === "string" &&
          typeof notice.details?.to_model === "string"
            ? `Demoted ${notice.details.from_model} → ${notice.details.to_model}`
            : `${notice.event_type}: ${notice.deployment_id ?? notice.model_definition_id ?? ""}`;
        setCapacityBanner(
          `${detail}${notice.reason ? ` (${notice.reason})` : ""} · ${notice.ts}`,
        );
      }
    }
    if (!selectedDeployment && routeData.deployments[0]) {
      setSelectedDeployment(routeData.deployments[0].id);
    }
    setLoadModelId((current) => {
      if (current) return current;
      const schedulable = modelRows.filter(isSchedulable);
      return schedulable[0]?.id ?? "";
    });
    setLoadWorkerId((current) => current || hardware.nodes[0]?.worker_id || "");
    const firstWorker = hardware.nodes[0]?.worker_id;
    if (firstWorker) {
      setOpenNodes((current) => (current.size ? current : new Set([firstWorker])));
    }
  }, [selectedDeployment, capacityBannerDismissed]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await refresh();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load models");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  useEffect(() => {
    void fetchModelRoots()
      .then((roots) => {
        setModelRoots(roots);
        const preferred = roots.find((root) => root.exists) ?? roots[0];
        if (preferred) setModelPath(preferred.path);
      })
      .catch(() => {
        /* Existing model management remains available if roots cannot be listed. */
      });
  }, []);

  useEffect(() => {
    if (!selectedDeployment) {
      setLogs([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const body = await fetchDeploymentLogs(selectedDeployment);
        if (!cancelled) setLogs(body.logs);
      } catch {
        if (!cancelled) setLogs([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedDeployment, deployments]);

  const fleetHasInFlightLoad = deployments.some((deployment) =>
    isInFlightLoad(deployment.status),
  );

  // Poll while this tab is waiting on a placement load, or any deployment is
  // still queued/loading, so the progress bar can advance mid-request.
  useEffect(() => {
    if (!placementLoading && !fleetHasInFlightLoad) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const routeData = await fetchRoutes();
        if (cancelled) return;
        setDeployments(routeData.deployments);
        setAliases(routeData.aliases);
      } catch {
        /* Keep the last known snapshot; the in-flight load call still owns errors. */
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [placementLoading, fleetHasInFlightLoad]);

  useEffect(() => {
    if (!placementLoading || loadStartedAt == null) {
      if (!placementLoading) {
        setLoadStartedAt(null);
        setLoadElapsedSec(0);
      }
      return;
    }
    const id = window.setInterval(() => {
      setLoadElapsedSec(Math.max(0, Math.floor((Date.now() - loadStartedAt) / 1000)));
    }, 500);
    return () => window.clearInterval(id);
  }, [placementLoading, loadStartedAt]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setConflict(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      if (err instanceof GatewayError) {
        const conflicting = err.details?.conflicting_deployment_ids;
        const contested = err.details?.contested_gpu_indexes;
        if (
          err.code === "GPU_PLACEMENT_CONFLICT" &&
          Array.isArray(conflicting) &&
          conflicting.length
        ) {
          setConflict({
            message: err.message,
            conflictingDeploymentIds: conflicting.map(String),
            contestedGpuIndexes: Array.isArray(contested)
              ? contested.map((value) => Number(value))
              : [],
          });
        }
        setError(`${err.message} [${err.code}] (${err.requestId})`);
      } else {
        setError(err instanceof Error ? err.message : "Action failed");
      }
    } finally {
      setBusy(false);
    }
  }

  async function scanFolder() {
    setBusy(true);
    setError(null);
    try {
      setDiscovered(await scanModelFolder(modelPath));
    } catch (err) {
      if (err instanceof GatewayError) {
        setError(`${err.message} [${err.code}] (${err.requestId})`);
      } else {
        setError(err instanceof Error ? err.message : "Folder scan failed");
      }
    } finally {
      setBusy(false);
    }
  }

  async function prepareDiscovered() {
    const pending = discovered.filter(
      (candidate) => candidate.recognized && candidate.status === "discovered",
    );
    setBusy(true);
    setError(null);
    try {
      const prepared = new Map<string, ModelFolderCandidate>();
      for (const candidate of pending) {
        prepared.set(candidate.path, await prepareModelFolder(candidate.path));
      }
      setDiscovered((items) => items.map((item) => prepared.get(item.path) ?? item));
    } catch (err) {
      if (err instanceof GatewayError) {
        setError(`${err.message} [${err.code}] (${err.requestId})`);
      } else {
        setError(err instanceof Error ? err.message : "Model preparation failed");
      }
    } finally {
      setBusy(false);
    }
  }

  const selected = deployments.find((item) => item.id === selectedDeployment) ?? null;
  const selectedDefinition = selected
    ? models.find((model) => model.id === selected.model_definition_id) ?? null
    : null;
  const selectedAlias = selected
    ? aliases.find((alias) => alias.deployment_id === selected.id)?.alias ?? null
    : null;
  const selectedCapabilities = new Set(selectedDefinition?.capabilities ?? []);
  const { textModels, generationModels } = useMemo(
    () => partitionModelDefinitions(models),
    [models],
  );
  const schedulableModels = useMemo(() => models.filter(isSchedulable), [models]);
  const { textModels: schedulableText, generationModels: schedulableGeneration } =
    useMemo(() => partitionModelDefinitions(schedulableModels), [schedulableModels]);
  const loadModel = schedulableModels.find((model) => model.id === loadModelId) ?? null;
  const loadNode = hardwareNodes.find((node) => node.worker_id === loadWorkerId) ?? null;
  const loadMode: PlacementMode =
    selectedGpus.length >= 2 ? "two_gpu" : "single_gpu";
  const pooledSelection = selectedGpus.length >= 2;
  const contextOptions = useMemo(
    () => allowedContextPresets(loadModel, loadMode),
    [loadModel, loadMode],
  );
  const resolvedContext = useMemo(
    () => resolveContextSelection(loadModel, loadMode, loadContextLength),
    [loadModel, loadMode, loadContextLength],
  );

  useEffect(() => {
    setLoadContextLength(resolveContextSelection(loadModel, loadMode, null));
  }, [loadModelId, loadMode, loadModel]);

  const plan = useMemo(
    () =>
      evaluatePlacement({
        model: loadModel,
        node: loadNode,
        mode: loadMode,
        gpuIndexes: selectedGpus,
        deployments,
        contextLength: resolvedContext,
      }),
    [deployments, loadMode, loadModel, loadNode, resolvedContext, selectedGpus],
  );

  const fleetGpuTotals = useMemo(() => {
    let total = 0;
    let held = 0;
    for (const node of hardwareNodes) {
      for (const gpu of listGpus(node)) {
        total += 1;
        if (occupantsForGpus(deployments, node.worker_id, [gpu.index]).length) {
          held += 1;
        }
      }
    }
    return { total, held, free: total - held };
  }, [deployments, hardwareNodes]);

  const readyDeployments = deployments.filter((deployment) =>
    READY_STATUSES.includes(deployment.status),
  );

  function selectGpuOnWorker(workerId: string, index: number) {
    setOpenNodes((current) => new Set(current).add(workerId));
    if (workerId !== loadWorkerId) {
      setLoadWorkerId(workerId);
      setSelectedGpus([index]);
      return;
    }
    setSelectedGpus((current) => {
      if (current.includes(index)) {
        return current.filter((value) => value !== index);
      }
      if (current.length >= 2) {
        return [current[1], index];
      }
      return [...current, index];
    });
  }

  function toggleNodeCard(workerId: string, open: boolean) {
    setOpenNodes((current) => {
      const next = new Set(current);
      if (open) next.add(workerId);
      else next.delete(workerId);
      return next;
    });
  }

  async function placeLoad() {
    if (plan.blocked || selectedGpus.length < 1) return;
    const warnings = plan.checks.filter((check) => check.level === "warn");
    if (warnings.length) {
      const proceed = window.confirm(
        [
          `Load ${loadModel?.display_name ?? loadModelId} on GPU ${plan.gpuIndexes.join(
            " + ",
          )} with ${formatCount(warnings.length, "unverified safeguard")}?`,
          ...warnings.map((check) => `• ${check.label}: ${check.detail}`),
        ].join("\n"),
      );
      if (!proceed) return;
    }
    setLoadStartedAt(Date.now());
    setLoadElapsedSec(0);
    setPlacementLoading(true);
    try {
      await run(async () => {
        const result = await loadDeployment({
          model_definition_id: loadModelId,
          worker_id: loadWorkerId,
          placement_mode: loadMode,
          gpu_indexes: plan.gpuIndexes,
          ...(resolvedContext != null ? { context_length: resolvedContext } : {}),
        });
        setSelectedDeployment(result.deployment.id);
      });
    } finally {
      setPlacementLoading(false);
    }
  }

  function unload(deploymentId: string) {
    void run(async () => {
      if (!window.confirm(`Unload deployment ${deploymentId}?`)) return;
      await unloadDeployment(deploymentId);
    });
  }

  function renderGpuTile(node: HardwareNode, gpu: GpuView) {
    const occupant =
      occupantsForGpus(deployments, node.worker_id, [gpu.index])[0] ?? null;
    const selected =
      node.worker_id === loadWorkerId && selectedGpus.includes(gpu.index);
    return (
      <GpuTile
        key={`${node.worker_id}-${gpu.index}`}
        gpu={gpu}
        occupant={occupant}
        busy={busy}
        selected={selected}
        pooled={selected && pooledSelection}
        onSelect={() => selectGpuOnWorker(node.worker_id, gpu.index)}
        onUnload={unload}
      />
    );
  }

  return (
    <div className="models-layout">
      <header className="models-header">
        <div>
          <h1>Models</h1>
          <p>
            Definitions, deployments, and aliases stay distinct. Lifecycle actions
            move through Yonder Gateway — never a runtime directly. Up to 3 lanes
            may share a GPU when measured VRAM fits; EnsureReady can auto-load
            and idle-evict (locks and the sole chat lane are protected).
          </p>
        </div>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={() => void run(async () => refresh())}
        >
          Refresh
        </button>
      </header>

      {error ? <div className="message-error models-error">{error}</div> : null}
      {capacityBanner ? (
        <div className="lab-banner" role="status">
          <span>{capacityBanner}</span>{" "}
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              setCapacityBanner(null);
              setCapacityBannerDismissed(true);
            }}
          >
            Dismiss
          </button>
        </div>
      ) : null}

      <section className="stat-strip" aria-label="Model fleet summary">
        <StatTile
          label="Schedulable models"
          value={`${schedulableModels.length}/${models.length}`}
          hint="validated and runtime-loadable"
          tone={schedulableModels.length ? "ok" : "neutral"}
        />
        <StatTile
          label="Deployments ready"
          value={`${readyDeployments.length}/${deployments.length}`}
          hint={
            deployments.length
              ? `${deployments.length - readyDeployments.length} not serving`
              : "none loaded"
          }
          tone={readyDeployments.length ? "ok" : "neutral"}
        />
        <StatTile
          label="GPUs free"
          value={
            fleetGpuTotals.total
              ? `${fleetGpuTotals.free}/${fleetGpuTotals.total}`
              : NOT_REPORTED
          }
          hint={
            fleetGpuTotals.total
              ? `${fleetGpuTotals.held} held by deployments`
              : "no GPU inventory reported"
          }
          tone={
            !fleetGpuTotals.total ? "neutral" : fleetGpuTotals.free ? "ok" : "warn"
          }
        />
        <StatTile
          label="Aliases"
          value={String(aliases.length)}
          hint={`${aliases.filter((alias) => alias.state === "enabled").length} enabled`}
          tone={aliases.length ? "ok" : "neutral"}
        />
      </section>

      {conflict ? (
        <section className="models-panel placement-conflict">
          <div className="panel-head">
            <h2>GPU placement conflict</h2>
            <TonePill tone="danger">gateway refused</TonePill>
          </div>
          <p className="entity-meta">{conflict.message}</p>
          <p className="entity-meta">
            Contested GPUs:{" "}
            {conflict.contestedGpuIndexes.length
              ? conflict.contestedGpuIndexes.join(", ")
              : NOT_REPORTED}
          </p>
          <div className="stack">
            {conflict.conflictingDeploymentIds.map((deploymentId) => (
              <div key={deploymentId} className="compact-static">
                <div className="compact-row-head">
                  <span className="compact-row-title">{deploymentId}</span>
                  <button
                    type="button"
                    className="btn btn-danger"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        if (
                          !window.confirm(`Unload conflicting deployment ${deploymentId}?`)
                        ) {
                          return;
                        }
                        await unloadDeployment(deploymentId);
                        setConflict(null);
                      })
                    }
                  >
                    Unload occupant
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="models-panel fleet-placement">
        <div className="panel-head">
          <h2>Fleet occupancy</h2>
          <span className="panel-count">
            {fleetGpuTotals.total
              ? `${fleetGpuTotals.free}/${fleetGpuTotals.total} free`
              : NOT_REPORTED}
          </span>
        </div>
        <p className="entity-meta panel-meta">
          Click GPUs on a host to place a model. Selecting two GPUs on the same
          host loads across both (2-GPU pool). Model and context are chosen on
          the active host; Gateway still validates every load.
        </p>

        {hardwareNodes.length === 0 ? (
          <p className="entity-meta">
            No hardware inventory yet. Start worker agents so GPUs can be listed.
          </p>
        ) : (
          <div className="node-grid fleet-host-grid">
            {hardwareNodes.map((node) => {
              const summary = summarizeNode(node);
              const gpus = listGpus(node);
              const active = node.worker_id === loadWorkerId;
              const showPoolHint = active && pooledSelection;
              return (
                <details
                  key={node.worker_id}
                  className="node-card fleet-host-card"
                  data-active={active}
                  open={openNodes.has(node.worker_id)}
                  onToggle={(event) =>
                    toggleNodeCard(node.worker_id, event.currentTarget.open)
                  }
                >
                  <summary className="node-summary fleet-host-head">
                    <div className="node-summary-head">
                      <div className="node-identity">
                        <strong>
                          {summary.hostname === NOT_REPORTED
                            ? summary.displayName
                            : summary.hostname}
                        </strong>
                        <span className="entity-meta">{summary.workerId}</span>
                      </div>
                      <TonePill tone={summary.statusTone}>{summary.statusLabel}</TonePill>
                    </div>
                    <Gauge
                      label={gpus.length ? `VRAM · ${formatCount(gpus.length, "GPU")}` : "VRAM"}
                      percent={summary.gpu.memoryPercent}
                      detail={
                        gpus.length
                          ? `${summary.gpu.modelLabel} · ${summary.gpu.memoryText}`
                          : `no GPUs reported (discovery: ${summary.gpu.discovery})`
                      }
                    />
                  </summary>

                  <div className="node-detail">
                    {summary.gpu.placementAvailable ? null : (
                      <p className="entity-meta">
                        GPUs unavailable for placement:{" "}
                        {summary.gpu.placementBlockedReason}. Honest inventory —
                        capacity is not invented.
                      </p>
                    )}
                    {gpus.length ? (
                      <>
                        <div className="gpu-tile-grid">
                          {gpus.map((gpu) => renderGpuTile(node, gpu))}
                        </div>
                        {showPoolHint ? (
                          <p className="gpu-pool-banner" role="status">
                            GPU Pool Selected — this model will load across both
                            GPUs.
                          </p>
                        ) : null}
                      </>
                    ) : null}

                    {active ? (
                      <div className="fleet-host-placement">
                        <div className="fleet-placement-strip">
                          <label className="field">
                            <span>Model</span>
                            <select
                              className="status-select"
                              value={loadModelId}
                              disabled={busy || schedulableModels.length === 0}
                              onChange={(event) =>
                                setLoadModelId(event.target.value)
                              }
                            >
                              {schedulableModels.length === 0 ? (
                                <option value="">
                                  No verified/loadable manifests
                                </option>
                              ) : (
                                <>
                                  {schedulableText.length > 0 ? (
                                    <optgroup label="Chat / embed / rerank">
                                      {schedulableText.map((model) => (
                                        <option key={model.id} value={model.id}>
                                          {model.display_name}
                                        </option>
                                      ))}
                                    </optgroup>
                                  ) : null}
                                  {schedulableGeneration.length > 0 ? (
                                    <optgroup label="Image / video gen">
                                      {schedulableGeneration.map((model) => (
                                        <option key={model.id} value={model.id}>
                                          {model.display_name}
                                        </option>
                                      ))}
                                    </optgroup>
                                  ) : null}
                                </>
                              )}
                            </select>
                          </label>
                          {loadModel && isGenerationModel(loadModel) ? (
                            <p className="muted fleet-gen-context-note">
                              Image/video loads skip context length — Diffusers
                              pipelines use their own prompt limits.
                            </p>
                          ) : (
                            <label className="field">
                              <span>Context length</span>
                              <select
                                className="status-select"
                                value={resolvedContext ?? ""}
                                disabled={busy || contextOptions.length === 0}
                                onChange={(event) =>
                                  setLoadContextLength(
                                    Number(event.target.value) || null,
                                  )
                                }
                              >
                                {contextOptions.length === 0 ? (
                                  <option value="">
                                    No context options for this mode
                                  </option>
                                ) : (
                                  contextOptions.map((preset) => (
                                    <option key={preset} value={preset}>
                                      {preset.toLocaleString()} tokens
                                    </option>
                                  ))
                                )}
                              </select>
                            </label>
                          )}
                          {loadModel ? (
                            <div className="placement-model-facts fleet-placement-facts">
                              <span>provider: {modelProvider(loadModel)}</span>
                              <span>
                                declared VRAM:{" "}
                                {minimumGpuMemoryBytes(loadModel, loadMode) ==
                                null
                                  ? NOT_REPORTED
                                  : formatBytes(
                                      minimumGpuMemoryBytes(
                                        loadModel,
                                        loadMode,
                                      ),
                                    )}
                              </span>
                              {isGenerationModel(loadModel) ? (
                                <span>
                                  profile:{" "}
                                  {String(
                                    (loadModel.manifest.runtime as
                                      | Record<string, unknown>
                                      | undefined)?.profile ?? "generation",
                                  )}
                                </span>
                              ) : (
                                <>
                                  <span>
                                    max ctx 1 GPU:{" "}
                                    {(() => {
                                      const max = modeMaxContextLength(
                                        loadModel,
                                        "single_gpu",
                                      );
                                      if (max == null) return NOT_REPORTED;
                                      return max <= 0
                                        ? "blocked"
                                        : max.toLocaleString();
                                    })()}
                                  </span>
                                  <span>
                                    max ctx 2 GPU:{" "}
                                    {(() => {
                                      const max = modeMaxContextLength(
                                        loadModel,
                                        "two_gpu",
                                      );
                                      if (max == null) return NOT_REPORTED;
                                      return max <= 0
                                        ? "blocked"
                                        : max.toLocaleString();
                                    })()}
                                  </span>
                                </>
                              )}
                            </div>
                          ) : null}
                        </div>

                        <div className="panel-head">
                          <h3 className="subhead">Preflight</h3>
                          <TonePill
                            tone={
                              plan.blocked
                                ? "danger"
                                : plan.requiresConfirmation
                                  ? "warn"
                                  : "ok"
                            }
                          >
                            {plan.blocked
                              ? "blocked"
                              : plan.requiresConfirmation
                                ? "confirm"
                                : "clear"}
                          </TonePill>
                        </div>
                        <ul className="check-list">
                          {plan.checks.map((check) => (
                            <li
                              key={check.id}
                              className="check-item"
                              data-level={check.level}
                            >
                              <TonePill tone={checkTone(check.level)}>
                                {check.level}
                              </TonePill>
                              <div>
                                <strong>{check.label}</strong>
                                <p className="entity-meta">{check.detail}</p>
                              </div>
                            </li>
                          ))}
                        </ul>
                        {(() => {
                          const inFlight =
                            deployments.find(
                              (deployment) =>
                                deployment.worker_id === node.worker_id &&
                                isInFlightLoad(deployment.status),
                            ) ?? null;
                          const loadingHere =
                            (placementLoading &&
                              node.worker_id === loadWorkerId) ||
                            inFlight != null;
                          const progress =
                            inFlight?.load_progress_percent != null
                              ? Number(inFlight.load_progress_percent)
                              : null;
                          const elapsedLabel =
                            loadElapsedSec > 0
                              ? `${loadElapsedSec}s`
                              : "starting";
                          return (
                            <>
                              {loadingHere ? (
                                <LoadProgressBar
                                  percent={progress}
                                  label="Loading model"
                                  detail={
                                    progress != null
                                      ? `${inFlight?.status ?? "loading"} · ${Math.round(progress)}% · ${elapsedLabel}`
                                      : `${inFlight?.status ?? "loading"} · ${elapsedLabel}`
                                  }
                                />
                              ) : null}
                              <button
                                type="button"
                                className="btn btn-primary placement-submit"
                                disabled={
                                  busy ||
                                  loadingHere ||
                                  plan.blocked ||
                                  selectedGpus.length < 1
                                }
                                onClick={() => void placeLoad()}
                              >
                                {loadingHere
                                  ? "Loading… please wait"
                                  : plan.blocked || selectedGpus.length < 1
                                    ? "Load blocked"
                                    : `Load onto GPU ${plan.gpuIndexes.join(" + ")}${
                                        loadModel && isGenerationModel(loadModel)
                                          ? ""
                                          : resolvedContext != null
                                            ? ` · ctx ${resolvedContext.toLocaleString()}`
                                            : ""
                                      }`}
                              </button>
                            </>
                          );
                        })()}
                        {plan.occupantDeploymentIds.length ? (
                          <div className="entity-actions">
                            {plan.occupantDeploymentIds.map((deploymentId) => (
                              <button
                                key={`preflight-unload-${deploymentId}`}
                                type="button"
                                className="btn btn-danger"
                                disabled={busy}
                                onClick={() => unload(deploymentId)}
                              >
                                Unload {deploymentId}
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ) : (
                      <p className="entity-meta">
                        Click a GPU on this host to choose model, context, and
                        load.
                      </p>
                    )}
                  </div>
                </details>
              );
            })}
          </div>
        )}
      </section>

      <section className="models-grid">
        <article className="models-panel">
          <div className="panel-head">
            <h2>Definitions</h2>
            <span className="panel-count">{models.length}</span>
          </div>
          <div className="stack">
            {models.length === 0 ? (
              <p className="entity-meta">
                No model definitions loaded from config/models.
              </p>
            ) : (
              <>
                {textModels.map((model) => (
                  <DefinitionRow
                    key={model.id}
                    model={model}
                    busy={busy}
                    onValidate={() =>
                      void run(async () => {
                        await validateModel(model.id);
                      })
                    }
                    onSelectForPlacement={() => setLoadModelId(model.id)}
                    onMockLoad={() =>
                      void run(async () => {
                        const result = await loadDeployment({
                          model_definition_id: model.id,
                        });
                        setSelectedDeployment(result.deployment.id);
                      })
                    }
                  />
                ))}
                {generationModels.length > 0 ? (
                  <>
                    <h3 className="subhead">Image / video gen</h3>
                    {generationModels.map((model) => (
                      <DefinitionRow
                        key={model.id}
                        model={model}
                        busy={busy}
                        onValidate={() =>
                          void run(async () => {
                            await validateModel(model.id);
                          })
                        }
                        onSelectForPlacement={() => setLoadModelId(model.id)}
                        onMockLoad={() =>
                          void run(async () => {
                            const result = await loadDeployment({
                              model_definition_id: model.id,
                            });
                            setSelectedDeployment(result.deployment.id);
                          })
                        }
                      />
                    ))}
                  </>
                ) : null}
              </>
            )}
          </div>
        </article>

        <article className="models-panel">
          <div className="panel-head">
            <h2>Deployments</h2>
            <span className="panel-count">
              {readyDeployments.length}/{deployments.length} ready
            </span>
          </div>
          <div className="stack">
            {deployments.length === 0 ? (
              <p className="entity-meta">
                No deployments yet. Load a definition to create one.
              </p>
            ) : (
              deployments.map((deployment) => (
                <button
                  key={deployment.id}
                  type="button"
                  className="compact-static compact-selectable"
                  data-active={deployment.id === selectedDeployment ? "true" : "false"}
                  aria-pressed={deployment.id === selectedDeployment}
                  onClick={() => setSelectedDeployment(deployment.id)}
                >
                  <div className="compact-row-head">
                    <span className="compact-row-title">{deployment.id}</span>
                    <TonePill tone={deploymentTone(deployment.status)}>
                      {deployment.status}
                      {deployment.load_progress_percent != null
                        ? ` ${deployment.load_progress_percent}%`
                        : ""}
                    </TonePill>
                  </div>
                  <p className="entity-meta">
                    {deployment.worker_id} · GPU{" "}
                    {deployment.gpu_assignments.length
                      ? deployment.gpu_assignments.join(", ")
                      : "none assigned"}{" "}
                    · {deployment.provider} · requests {deployment.request_count}
                    {deployment.runtime_configuration?.locked ? " · locked" : ""}
                  </p>
                  {isInFlightLoad(deployment.status) ? (
                    <LoadProgressBar
                      percent={
                        deployment.load_progress_percent != null
                          ? Number(deployment.load_progress_percent)
                          : null
                      }
                      label="Load progress"
                      detail={
                        deployment.load_progress_percent != null
                          ? `${Math.round(Number(deployment.load_progress_percent))}%`
                          : deployment.status
                      }
                    />
                  ) : null}
                  {deployment.failure_reason ? (
                    <div className="message-error">{deployment.failure_reason}</div>
                  ) : null}
                </button>
              ))
            )}
          </div>

          {selected ? (
            <div className="entity-actions">
              <label className="entity-meta" style={{ display: "inline-flex", gap: "0.4rem", alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={Boolean(selected.runtime_configuration?.locked)}
                  disabled={busy}
                  onChange={(event) =>
                    void run(async () => {
                      await setDeploymentLock(selected.id, event.target.checked);
                    })
                  }
                />
                Lock lane (EnsureReady will not unload)
              </label>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    if (
                      !window.confirm(
                        `Drain deployment ${selected.id}? New requests will be rejected.`,
                      )
                    ) {
                      return;
                    }
                    await drainDeployment(selected.id);
                  })
                }
              >
                Drain
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy}
                onClick={() => unload(selected.id)}
              >
                Unload
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await loadDeployment({ deployment_id: selected.id });
                  })
                }
              >
                Reload
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await retryDeployment(selected.id);
                  })
                }
              >
                Retry
              </button>
              {selectedAlias && selectedCapabilities.has("chat") ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const result = await runTestPrompt(
                        selectedAlias,
                        "Yonder test prompt",
                      );
                      setTestOutput(result.response.choices[0]?.message.content ?? "");
                    })
                  }
                >
                  Test chat
                </button>
              ) : null}
              {selectedAlias &&
              (selectedCapabilities.has("embeddings") ||
                selectedCapabilities.has("embedding")) ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const result = await runEmbeddingTest(
                        selectedAlias,
                        "Yonder embedding canary",
                      );
                      setTestOutput(
                        `Embedding dimensions: ${result.data[0]?.embedding.length ?? 0}`,
                      );
                    })
                  }
                >
                  Test embed
                </button>
              ) : null}
              {selectedAlias &&
              (selectedCapabilities.has("reranking") ||
                selectedCapabilities.has("rerank")) ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const result = await runRerankTest(selectedAlias);
                      setTestOutput(
                        `Top rerank score: ${result.results[0]?.relevance_score ?? NOT_REPORTED}`,
                      );
                    })
                  }
                >
                  Test rerank
                </button>
              ) : null}
              {selectedCapabilities.has("image_generation") ||
              selectedCapabilities.has("image") ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const result = await runGenerationTest("txt2img");
                      setTestOutput(
                        `Image job ${result.job.id}: ${result.job.status} (${result.job.availability})`,
                      );
                    })
                  }
                >
                  Test image
                </button>
              ) : null}
              {selectedCapabilities.has("video_generation") ||
              selectedCapabilities.has("video") ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const result = await runGenerationTest("txt2video");
                      setTestOutput(
                        `Video job ${result.job.id}: ${result.job.status} (${result.job.availability})`,
                      );
                    })
                  }
                >
                  Test video
                </button>
              ) : null}
            </div>
          ) : null}

          {testOutput ? (
            <div className="compact-static" style={{ marginTop: "0.75rem" }}>
              <div className="compact-row-head">
                <span className="compact-row-title">Test result</span>
              </div>
              <p className="entity-meta">{testOutput}</p>
            </div>
          ) : null}
        </article>

        <article className="models-panel">
          <div className="panel-head">
            <h2>Aliases</h2>
            <span className="panel-count">{aliases.length}</span>
          </div>
          <div className="stack">
            {aliases.length === 0 ? (
              <p className="entity-meta">No model aliases configured.</p>
            ) : (
              aliases.map((alias) => (
                <details key={alias.alias} className="compact-row">
                  <summary>
                    <span className="compact-row-title">{alias.alias}</span>
                    <span className={statusClass(alias.state)}>{alias.state}</span>
                  </summary>
                  <div className="compact-row-body">
                    <p className="entity-meta">
                      deployment: {alias.deployment_id}
                      {alias.experimental ? " · experimental" : ""}
                    </p>
                    <label className="field">
                      <span>Repoint to deployment</span>
                      <select
                        className="status-select"
                        value={alias.deployment_id}
                        disabled={busy}
                        onChange={(event) =>
                          void run(async () => {
                            await repointAlias(alias.alias, event.target.value);
                          })
                        }
                      >
                        {deployments.map((deployment) => (
                          <option key={deployment.id} value={deployment.id}>
                            {deployment.id} ({deployment.status})
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </details>
              ))
            )}
          </div>

          <h3 className="subhead">Deployment logs</h3>
          <pre className="logs-panel">
            {logs.length ? logs.join("\n") : "No logs for the selected deployment."}
          </pre>
        </article>
      </section>

      <section className="models-panel">
        <details className="ops-collapsible">
          <summary>
            <span className="ops-collapsible-title">Model inventory (not readiness)</span>
            <span className="panel-count">{discovered.length}</span>
          </summary>
          <div className="ops-collapsible-body">
            <p className="entity-meta">
              Point Yonder at an allowlisted server-side folder. The Gateway detects
              model type and format, proposes stable aliases, checksums the files, and
              writes <code>bundle.yaml</code>. These rows are inventory only. Runtime
              activation remains disabled until conversion, host canary, and manifest
              validation succeed.
            </p>
            <div className="toolbar-row">
              <input
                className="status-select"
                style={{ flex: 1, minWidth: "18rem" }}
                value={modelPath}
                list="model-roots"
                onChange={(event) => setModelPath(event.target.value)}
                placeholder="/srv/yonder/models"
              />
              <datalist id="model-roots">
                {modelRoots.map((root) => (
                  <option key={root.path} value={root.path} />
                ))}
              </datalist>
              <button
                type="button"
                className="btn"
                disabled={busy || !modelPath}
                onClick={() => void scanFolder()}
              >
                Scan folder
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy || discovered.every((item) => item.status !== "discovered")}
                onClick={() => void prepareDiscovered()}
              >
                Prepare discovered
              </button>
            </div>
            {discovered.length ? (
              <div className="stack" style={{ marginTop: "0.75rem" }}>
                {discovered.map((candidate) => (
                  <details key={candidate.path} className="compact-row">
                    <summary>
                      <span className="compact-row-title">{candidate.display_name}</span>
                      <span className={statusClass(candidate.status)}>
                        {candidate.status}
                      </span>
                    </summary>
                    <div className="compact-row-body">
                      <p className="entity-meta">
                        format: {candidate.model_format} · modality: {candidate.modality} ·
                        size {formatBytes(candidate.size_bytes)}
                      </p>
                      <p className="entity-meta">
                        runtime candidate: {candidate.runtime_candidate} · readiness:
                        inventory/unverified
                      </p>
                      <p className="entity-meta">
                        proposed aliases: {candidate.suggested_aliases.join(", ")}
                      </p>
                      {candidate.issues.map((issue) => (
                        <p key={`${candidate.path}-${issue.code}`} className="entity-meta">
                          {issue.code}: {issue.message}
                        </p>
                      ))}
                    </div>
                  </details>
                ))}
              </div>
            ) : null}
          </div>
        </details>
      </section>

      <ModelLabSheet />
    </div>
  );
}
