/**
 * Client-side placement preflight.
 *
 * The Gateway remains the authority on placement: it can still refuse a load
 * with `GPU_PLACEMENT_CONFLICT` or `INVENTORY_UNAVAILABLE`. These checks exist
 * so an operator sees the safeguards *before* pressing load. They never invent
 * capacity: an unreported metric fails closed the same way admission does,
 * rather than being treated as an assumed pass.
 */

import type { DeploymentView, ModelDefinition } from "@/lib/admin";
import type { HardwareNode } from "@/lib/hardware";
import { listGpus } from "@/lib/hardware";
import { formatBytes } from "@/lib/format";

export type PlacementMode = "single_gpu" | "two_gpu";

export type PlacementCheckLevel = "ok" | "warn" | "block";

export type PlacementCheck = {
  id: string;
  label: string;
  level: PlacementCheckLevel;
  detail: string;
};

export type PlacementPlan = {
  gpuIndexes: number[];
  checks: PlacementCheck[];
  blocked: boolean;
  requiresConfirmation: boolean;
  occupantDeploymentIds: string[];
  requiredBytes: number | null;
  freeBytes: number | null;
  maxContextLength: number | null;
  contextLength: number | null;
};

/** Deployment states that still hold their GPU assignment. */
export const OCCUPYING_STATUSES = [
  "ready",
  "loading",
  "queued",
  "validating",
  "draining",
];

const LLAMA_PROVIDERS = [
  "llama-cpp",
  "llama-cpp-embeddings",
  "llama-cpp-rerank",
];

/**
 * The runtime keeps headroom on each device for KV cache growth and CUDA
 * context. This mirrors the Gateway default; the deployed value is server-side
 * configuration the console cannot read, so the Gateway stays authoritative.
 */
export const VRAM_RESERVE_BYTES = 1024 ** 3;

/**
 * Discrete context presets offered in the placement console.
 * Measured mode maxes (including non-power-of-two values like 98304 / 163840)
 * are always unioned in via {@link allowedContextPresets}.
 */
export const CONTEXT_PRESETS = [
  2048, 4096, 8192, 16384, 32768, 65536, 98304, 131072, 163840, 262144,
] as const;

function manifestSection(
  model: ModelDefinition,
  key: string,
): Record<string, unknown> | undefined {
  return model.manifest[key] as Record<string, unknown> | undefined;
}

export function modelProvider(model: ModelDefinition): string {
  return String(manifestSection(model, "runtime")?.provider ?? "");
}

/** Image/video Diffusers (and mock generation) definitions — listed under gen. */
export function isGenerationModel(model: ModelDefinition): boolean {
  const provider = modelProvider(model);
  if (provider === "diffusers" || provider === "mock-generation") return true;
  const profile = String(manifestSection(model, "runtime")?.profile ?? "").toLowerCase();
  if (profile === "image" || profile === "video") return true;
  const caps = (model.capabilities ?? []).map((cap) => String(cap).toLowerCase());
  return caps.some((cap) =>
    ["image", "video", "image_generation", "video_generation"].includes(cap),
  );
}

/** Chat/embed/rerank first; image/video generation last. */
export function partitionModelDefinitions(models: ModelDefinition[]): {
  textModels: ModelDefinition[];
  generationModels: ModelDefinition[];
} {
  const textModels: ModelDefinition[] = [];
  const generationModels: ModelDefinition[] = [];
  for (const model of models) {
    if (isGenerationModel(model)) generationModels.push(model);
    else textModels.push(model);
  }
  return { textModels, generationModels };
}

type ModeLimits = {
  max_context_length?: number;
  minimum_gpu_memory_gb?: number;
  evidence?: string;
};

type PlacementLimits = {
  single_gpu?: ModeLimits | null;
  two_gpu?: ModeLimits | null;
};

export function placementLimits(model: ModelDefinition | null): PlacementLimits | null {
  if (!model) return null;
  const raw = manifestSection(model, "requirements")?.placement_limits;
  if (!raw || typeof raw !== "object") return null;
  return raw as PlacementLimits;
}

export function modeMaxContextLength(
  model: ModelDefinition | null,
  mode: PlacementMode,
): number | null {
  const limits = placementLimits(model);
  if (!limits) return null;
  const modeLimit = mode === "single_gpu" ? limits.single_gpu : limits.two_gpu;
  if (!modeLimit || typeof modeLimit.max_context_length !== "number") return null;
  return modeLimit.max_context_length;
}

export function modeAllowed(model: ModelDefinition | null, mode: PlacementMode): boolean {
  const limits = placementLimits(model);
  if (!limits) return true;
  const max = modeMaxContextLength(model, mode);
  return max != null && max > 0;
}

export function defaultContextLength(model: ModelDefinition | null): number | null {
  if (!model) return null;
  const raw = manifestSection(model, "runtime")?.context_length;
  const value = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(value) || value < 1) return null;
  return value;
}

export function allowedContextPresets(
  model: ModelDefinition | null,
  mode: PlacementMode,
): number[] {
  // Image/video Diffusers loads do not expose a chat-style context picker.
  if (model && isGenerationModel(model)) return [];

  const max = modeMaxContextLength(model, mode);
  if (max == null) {
    // Legacy manifests without placement_limits: offer defaults up to model default.
    const fallback = defaultContextLength(model) ?? 8192;
    return CONTEXT_PRESETS.filter((preset) => preset <= Math.max(fallback, 8192));
  }
  if (max <= 0) return [];
  // Presets at or under the mode max, plus the measured max itself when it is
  // not already a listed preset (so operators can always load absolute safe max).
  const values = new Set<number>(
    CONTEXT_PRESETS.filter((preset) => preset <= max),
  );
  values.add(max);
  const fallback = defaultContextLength(model);
  if (fallback != null && fallback > 0 && fallback <= max) values.add(fallback);
  return [...values].sort((a, b) => a - b);
}

export function resolveContextSelection(
  model: ModelDefinition | null,
  mode: PlacementMode,
  selected: number | null,
): number | null {
  // Generation pipelines keep a manifest token bound for Gateway bookkeeping,
  // but operators do not choose it at load time.
  if (model && isGenerationModel(model)) {
    return defaultContextLength(model);
  }

  const allowed = allowedContextPresets(model, mode);
  if (!allowed.length) return null;
  if (selected != null && allowed.includes(selected)) return selected;
  const preferred = defaultContextLength(model);
  if (preferred != null && allowed.includes(preferred)) return preferred;
  const max = modeMaxContextLength(model, mode);
  if (max != null && max > 0) {
    const underMax = [...allowed].reverse().find((preset) => preset <= max);
    if (underMax != null) return underMax;
  }
  return allowed[allowed.length - 1] ?? null;
}

/**
 * A definition is schedulable only when validation passed and the runtime is one
 * Yonder can place on a GPU. Mock providers stay out of this surface; Mac-local
 * mock lanes remain reachable through development aliases.
 */
export function isSchedulable(model: ModelDefinition): boolean {
  const provider = modelProvider(model);
  const sourcePath = String(manifestSection(model, "source")?.path ?? "").toLowerCase();
  const validated = ["valid", "ok"].includes(model.validation_status);
  const llamaLoadable =
    LLAMA_PROVIDERS.includes(provider) && sourcePath.endsWith(".gguf");
  return validated && (llamaLoadable || provider === "diffusers");
}

export function minimumGpuMemoryBytes(
  model: ModelDefinition,
  mode?: PlacementMode,
): number | null {
  const limits = placementLimits(model);
  if (mode && limits) {
    const modeLimit = mode === "single_gpu" ? limits.single_gpu : limits.two_gpu;
    const modeGb = modeLimit?.minimum_gpu_memory_gb;
    if (typeof modeGb === "number" && Number.isFinite(modeGb) && modeGb > 0) {
      return modeGb * 1024 ** 3;
    }
  }
  const requirement = manifestSection(model, "requirements")?.minimum_gpu_memory_gb;
  const gb = typeof requirement === "number" ? requirement : Number(requirement);
  if (!Number.isFinite(gb) || gb <= 0) return null;
  return gb * 1024 ** 3;
}

export function occupantsForGpus(
  deployments: DeploymentView[],
  workerId: string,
  gpuIndexes: number[],
): DeploymentView[] {
  return deployments.filter(
    (deployment) =>
      deployment.worker_id === workerId &&
      OCCUPYING_STATUSES.includes(deployment.status) &&
      deployment.gpu_assignments.some((index) => gpuIndexes.includes(index)),
  );
}

export function requestedGpuIndexes(
  mode: PlacementMode,
  gpuA: number,
  gpuB: number,
): number[] {
  return mode === "single_gpu" ? [gpuA] : [gpuA, gpuB];
}

export function evaluatePlacement(input: {
  model: ModelDefinition | null;
  node: HardwareNode | null;
  mode: PlacementMode;
  gpuIndexes: number[];
  deployments: DeploymentView[];
  contextLength?: number | null;
}): PlacementPlan {
  const { model, node, mode, gpuIndexes, deployments } = input;
  const checks: PlacementCheck[] = [];
  const expectedCount = mode === "single_gpu" ? 1 : 2;
  const maxContextLength = modeMaxContextLength(model, mode);
  const requestedContextLength =
    input.contextLength != null && Number.isFinite(input.contextLength)
      ? Number(input.contextLength)
      : null;
  const contextLength = resolveContextSelection(
    model,
    mode,
    requestedContextLength,
  );
  const lengthForHardCap =
    requestedContextLength != null ? requestedContextLength : contextLength;

  if (!model) {
    checks.push({
      id: "model",
      label: "Model definition",
      level: "block",
      detail: "No schedulable definition selected.",
    });
  } else if (!isSchedulable(model)) {
    checks.push({
      id: "model",
      label: "Model definition",
      level: "block",
      detail: `${model.display_name} is inventory/unverified (validation: ${model.validation_status}).`,
    });
  } else {
    checks.push({
      id: "model",
      label: "Model definition",
      level: "ok",
      detail: `${model.display_name} validated for ${modelProvider(model)}.`,
    });
  }

  if (model && placementLimits(model) && !modeAllowed(model, mode)) {
    checks.push({
      id: "placement_mode",
      label: "Placement mode",
      level: "block",
      detail:
        mode === "single_gpu"
          ? "This model cannot load on a single GPU (weights exceed one V100 32GB). Use a 2-GPU pool."
          : "This model has no two-GPU placement limit configured.",
    });
  } else if (model) {
    checks.push({
      id: "placement_mode",
      label: "Placement mode",
      level: "ok",
      detail:
        maxContextLength == null
          ? `${mode} allowed (no curated context hard-cap in manifest).`
          : `${mode} allowed · max context ${maxContextLength.toLocaleString()} tokens.`,
    });
  }

  if (model && isGenerationModel(model) && modeAllowed(model, mode)) {
    checks.push({
      id: "context_length",
      label: "Context length",
      level: "ok",
      detail: "Not used for image/video generation loads (pipeline defaults apply).",
    });
  } else if (model && modeAllowed(model, mode)) {
    if (lengthForHardCap == null) {
      checks.push({
        id: "context_length",
        label: "Context length",
        level: "block",
        detail: "Select a context length within the hard limit for this placement mode.",
      });
    } else if (maxContextLength != null && lengthForHardCap > maxContextLength) {
      checks.push({
        id: "context_length",
        label: "Context length",
        level: "block",
        detail: `Requested ${lengthForHardCap.toLocaleString()} exceeds the ${mode} hard limit of ${maxContextLength.toLocaleString()}.`,
      });
    } else {
      checks.push({
        id: "context_length",
        label: "Context length",
        level: "ok",
        detail:
          maxContextLength == null
            ? `Context ${lengthForHardCap.toLocaleString()} tokens.`
            : `Context ${lengthForHardCap.toLocaleString()} tokens (hard max ${maxContextLength.toLocaleString()}).`,
      });
    }
  }

  if (!node) {
    checks.push({
      id: "worker",
      label: "Worker",
      level: "block",
      detail: "No worker selected. Placement requires a heartbeating worker.",
    });
  } else if (node.maintenance) {
    checks.push({
      id: "worker",
      label: "Worker",
      level: "block",
      detail: `${node.display_name || node.worker_id} is in maintenance. Remove maintenance before loading.`,
    });
  } else if (!node.connected) {
    checks.push({
      id: "worker",
      label: "Worker",
      level: "block",
      detail: `${node.display_name || node.worker_id} is worker_disconnected.`,
    });
  } else {
    checks.push({
      id: "worker",
      label: "Worker",
      level: node.ready ? "ok" : "warn",
      detail: node.ready
        ? `${node.display_name || node.worker_id} connected and ready.`
        : `${node.display_name || node.worker_id} connected but not reporting ready.`,
    });
  }

  const discovery = node?.hardware?.gpu_discovery_status ?? "not_reported";
  const inventory = node ? listGpus(node) : [];
  const inventoryIndexes = inventory.map((gpu) => gpu.index);
  const distinct = [...new Set(gpuIndexes)];
  const unknownIndexes = distinct.filter(
    (index) => !inventoryIndexes.includes(index),
  );

  if (distinct.length !== expectedCount) {
    checks.push({
      id: "gpu_selection",
      label: "GPU selection",
      level: "block",
      detail:
        mode === "two_gpu"
          ? "A two-GPU pool needs two different GPU indexes."
          : "Select exactly one GPU index.",
    });
  } else if (node && inventory.length === 0) {
    checks.push({
      id: "gpu_selection",
      label: "GPU selection",
      level: "block",
      detail: `No GPUs reported for this worker (discovery: ${discovery}).`,
    });
  } else if (unknownIndexes.length) {
    checks.push({
      id: "gpu_selection",
      label: "GPU selection",
      level: "block",
      detail: `GPU ${unknownIndexes.join(", ")} is not in the reported inventory.`,
    });
  } else {
    checks.push({
      id: "gpu_selection",
      label: "GPU selection",
      level: "ok",
      detail: `GPU ${distinct.join(" + ")} on ${node?.display_name || node?.worker_id}.`,
    });
  }

  const occupants = node ? occupantsForGpus(deployments, node.worker_id, distinct) : [];
  const maxLanes = 3;
  const laneFull = distinct.some((gpuIndex) => {
    const onGpu = occupants.filter((deployment) =>
      deployment.gpu_assignments.includes(gpuIndex),
    );
    return onGpu.length >= maxLanes;
  });
  if (laneFull) {
    checks.push({
      id: "gpu_occupancy",
      label: "GPU lanes",
      level: "block",
      detail: `Selected GPU(s) are at the lane cap (${maxLanes} deployments per GPU). Unload an idle lane or pick another GPU.`,
    });
  } else if (occupants.length) {
    const laneSummary = distinct
      .map((gpuIndex) => {
        const count = occupants.filter((deployment) =>
          deployment.gpu_assignments.includes(gpuIndex),
        ).length;
        return `GPU ${gpuIndex}: ${count}/${maxLanes}`;
      })
      .join("; ");
    checks.push({
      id: "gpu_occupancy",
      label: "GPU lanes",
      level: "warn",
      detail: `${laneSummary}. Sharing is allowed while measured VRAM still fits.`,
    });
  } else if (node && distinct.length === expectedCount && !unknownIndexes.length) {
    checks.push({
      id: "gpu_occupancy",
      label: "GPU lanes",
      level: "ok",
      detail: `No Yonder deployments on selected GPUs (0/${maxLanes} lanes used).`,
    });
  }

  const selected = inventory.filter((gpu) => distinct.includes(gpu.index));
  const unmeasured = selected.filter(
    (gpu) => gpu.availableBytes == null || gpu.totalBytes == null || !gpu.indexReported,
  );
  const freeBytes =
    selected.length && unmeasured.length === 0
      ? selected.reduce((sum, gpu) => sum + (gpu.availableBytes ?? 0), 0)
      : null;
  const perGpuBytes = model ? minimumGpuMemoryBytes(model, mode) : null;
  const requiredBytes =
    perGpuBytes == null ? null : (perGpuBytes + VRAM_RESERVE_BYTES) * distinct.length;

  if (perGpuBytes == null) {
    checks.push({
      id: "vram_headroom",
      label: "VRAM headroom",
      level: "warn",
      detail:
        "Manifest declares no minimum_gpu_memory_gb, so the Gateway performs no VRAM fit check for this load.",
    });
  } else if (!selected.length) {
    checks.push({
      id: "vram_headroom",
      label: "VRAM headroom",
      level: "block",
      detail: "No reported GPU matches the selection, so headroom cannot be measured.",
    });
  } else if (unmeasured.length) {
    checks.push({
      id: "vram_headroom",
      label: "VRAM headroom",
      level: "block",
      detail: `GPU ${unmeasured
        .map((gpu) => gpu.index)
        .join(", ")} did not report measured free VRAM. Gateway admission fails closed rather than assuming capacity.`,
    });
  } else {
    const perGpuNeeded = perGpuBytes + VRAM_RESERVE_BYTES;
    const short = selected.filter((gpu) => (gpu.availableBytes ?? 0) < perGpuNeeded);
    if (short.length) {
      checks.push({
        id: "vram_headroom",
        label: "VRAM headroom",
        level: "block",
        detail: `${short
          .map((gpu) => `GPU ${gpu.index} has ${formatBytes(gpu.availableBytes)} free`)
          .join("; ")} — each selected GPU needs ${formatBytes(
          perGpuBytes,
        )} plus a ${formatBytes(VRAM_RESERVE_BYTES)} runtime reserve.`,
      });
    } else {
      checks.push({
        id: "vram_headroom",
        label: "VRAM headroom",
        level: "ok",
        detail: `${formatBytes(freeBytes)} measured free across ${distinct.length} GPU${
          distinct.length === 1 ? "" : "s"
        } covers ${formatBytes(perGpuBytes)} plus a ${formatBytes(
          VRAM_RESERVE_BYTES,
        )} reserve per GPU.`,
      });
    }
  }

  if (node && discovery !== "available") {
    checks.push({
      id: "gpu_inventory",
      label: "GPU inventory",
      level: "block",
      detail: `Discovery reports ${discovery}${
        node.hardware?.gpu_discovery_message
          ? ` — ${node.hardware.gpu_discovery_message}`
          : ""
      }. The Gateway treats a non-available discovery status as no usable inventory.`,
    });
  }

  return {
    gpuIndexes: distinct,
    checks,
    blocked: checks.some((check) => check.level === "block"),
    requiresConfirmation: checks.some((check) => check.level === "warn"),
    occupantDeploymentIds: occupants.map((deployment) => deployment.id),
    requiredBytes,
    freeBytes,
    maxContextLength,
    contextLength,
  };
}
