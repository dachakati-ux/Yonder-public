import { describe, expect, it } from "vitest";

import type { DeploymentView, ModelDefinition } from "./admin";
import type { HardwareNode } from "./hardware";
import {
  allowedContextPresets,
  evaluatePlacement,
  isGenerationModel,
  isSchedulable,
  minimumGpuMemoryBytes,
  modeAllowed,
  occupantsForGpus,
  partitionModelDefinitions,
  requestedGpuIndexes,
} from "./placement";

function available(value: string | number) {
  return { availability: "available", value };
}

function gpu(index: number, usedGib: number, totalGib = 16) {
  return {
    index: available(index),
    name: available("Tesla V100-SXM2-16GB"),
    memory_used_bytes: available(usedGib * 1024 ** 3),
    memory_total_bytes: available(totalGib * 1024 ** 3),
    memory_available_bytes: available((totalGib - usedGib) * 1024 ** 3),
  };
}

function node(overrides: Partial<HardwareNode> = {}): HardwareNode {
  return {
    worker_id: "worker-ai1",
    display_name: "AI1",
    connected: true,
    ready: true,
    status: "connected",
    source: "worker_heartbeat",
    hardware: {
      gpu_discovery_status: "available",
      host: { hostname: available("ai1") },
      gpus: [gpu(0, 1), gpu(1, 1)],
    },
    ...overrides,
  };
}

function model(overrides: Partial<ModelDefinition> = {}): ModelDefinition {
  return {
    id: "yonder-qwen35-9b",
    display_name: "Qwen3.5 9B",
    description: "",
    capabilities: ["chat"],
    validation_status: "valid",
    validation_issues: [],
    manifest: {
      runtime: { provider: "llama-cpp", model_identifier: "qwen35-9b" },
      source: { path: "/srv/yonder/models/qwen35-9b/model.gguf" },
      requirements: { minimum_gpu_memory_gb: 12 },
    },
    ...overrides,
  };
}

function deployment(overrides: Partial<DeploymentView> = {}): DeploymentView {
  return {
    id: "yonder-qwen36-primary",
    model_definition_id: "yonder-qwen36-35b-a3b-q4",
    worker_id: "worker-ai1",
    provider: "llama-cpp",
    status: "ready",
    gpu_assignments: [0],
    request_count: 0,
    active_requests: 0,
    logs: [],
    ...overrides,
  };
}

function check(plan: ReturnType<typeof evaluatePlacement>, id: string) {
  const found = plan.checks.find((item) => item.id === id);
  if (!found) throw new Error(`missing check ${id}`);
  return found;
}

describe("context hard limits", () => {
  function limitedModel(): ModelDefinition {
    return model({
      manifest: {
        runtime: {
          provider: "llama-cpp",
          model_identifier: "qwen35-9b",
          context_length: 8192,
        },
        source: { path: "/srv/yonder/models/qwen35-9b/model.gguf" },
        requirements: {
          minimum_gpu_memory_gb: 12,
          placement_limits: {
            single_gpu: { max_context_length: 32768, minimum_gpu_memory_gb: 12 },
            two_gpu: { max_context_length: 65536, minimum_gpu_memory_gb: 12 },
          },
        },
      },
    });
  }

  function coderModel(): ModelDefinition {
    return model({
      id: "yonder-qwen3-coder-next-q4",
      display_name: "Coder Next",
      manifest: {
        runtime: {
          provider: "llama-cpp",
          model_identifier: "coder",
          context_length: 8192,
          tensor_parallel_size: 2,
        },
        source: { path: "/srv/yonder/models/coder/model.gguf" },
        requirements: {
          minimum_gpu_memory_gb: 24,
          placement_limits: {
            single_gpu: { max_context_length: 0 },
            two_gpu: { max_context_length: 8192, minimum_gpu_memory_gb: 24 },
          },
        },
      },
    });
  }

  it("filters presets to the mode hard max", () => {
    expect(allowedContextPresets(limitedModel(), "single_gpu")).toEqual([
      2048, 4096, 8192, 16384, 32768,
    ]);
    expect(allowedContextPresets(limitedModel(), "two_gpu")).toContain(65536);
  });

  it("offers measured absolute max including 262144 and non-power-of-two caps", () => {
    const high = model({
      manifest: {
        runtime: {
          provider: "llama-cpp",
          model_identifier: "qwen36-35b",
          context_length: 8192,
        },
        source: { path: "/srv/yonder/models/qwen36/model.gguf" },
        requirements: {
          minimum_gpu_memory_gb: 24,
          placement_limits: {
            single_gpu: { max_context_length: 262144, minimum_gpu_memory_gb: 24 },
            two_gpu: { max_context_length: 262144, minimum_gpu_memory_gb: 24 },
          },
        },
      },
    });
    const presets1 = allowedContextPresets(high, "single_gpu");
    expect(presets1).toContain(131072);
    expect(presets1).toContain(163840);
    expect(presets1[presets1.length - 1]).toBe(262144);

    const odd = model({
      manifest: {
        runtime: {
          provider: "llama-cpp",
          model_identifier: "gemma4-31b",
          context_length: 8192,
        },
        source: { path: "/srv/yonder/models/gemma31/model.gguf" },
        requirements: {
          minimum_gpu_memory_gb: 20,
          placement_limits: {
            single_gpu: { max_context_length: 98304, minimum_gpu_memory_gb: 20 },
            two_gpu: { max_context_length: 262144, minimum_gpu_memory_gb: 20 },
          },
        },
      },
    });
    expect(allowedContextPresets(odd, "single_gpu")).toContain(98304);
    expect(allowedContextPresets(odd, "single_gpu")).not.toContain(131072);
    expect(allowedContextPresets(odd, "two_gpu")).toContain(262144);
  });

  it("blocks single-GPU for models with max_context_length 0", () => {
    expect(modeAllowed(coderModel(), "single_gpu")).toBe(false);
    expect(modeAllowed(coderModel(), "two_gpu")).toBe(true);
    const plan = evaluatePlacement({
      model: coderModel(),
      node: node(),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
      contextLength: 8192,
    });
    expect(check(plan, "placement_mode").level).toBe("block");
    expect(plan.blocked).toBe(true);
  });

  it("blocks context above the hard max", () => {
    const plan = evaluatePlacement({
      model: limitedModel(),
      node: node(),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
      contextLength: 65536,
    });
    expect(check(plan, "context_length").level).toBe("block");
  });

  it("accepts a preset within the hard max", () => {
    const plan = evaluatePlacement({
      model: limitedModel(),
      node: node(),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
      contextLength: 16384,
    });
    expect(check(plan, "context_length").level).toBe("ok");
    expect(plan.contextLength).toBe(16384);
    expect(plan.maxContextLength).toBe(32768);
  });

  it("does not require a chat context picker for Diffusers image/video", () => {
    const sdxl = model({
      id: "yonder-sdxl-turbo",
      display_name: "SDXL Turbo",
      capabilities: ["image"],
      manifest: {
        runtime: {
          provider: "diffusers",
          profile: "image",
          model_identifier: "sdxl-turbo",
          context_length: 77,
        },
        source: { path: "/srv/yonder-storage/model-bundles/sdxl" },
        requirements: {
          minimum_gpu_memory_gb: 12,
          placement_limits: {
            single_gpu: { max_context_length: 77, minimum_gpu_memory_gb: 12 },
            two_gpu: { max_context_length: 77, minimum_gpu_memory_gb: 12 },
          },
        },
      },
    });
    expect(allowedContextPresets(sdxl, "single_gpu")).toEqual([]);
    const plan = evaluatePlacement({
      model: sdxl,
      node: node(),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
      contextLength: null,
    });
    expect(check(plan, "context_length").level).toBe("ok");
    expect(plan.blocked).toBe(false);
    expect(plan.contextLength).toBe(77);
  });
});

describe("isSchedulable", () => {
  it("accepts validated GGUF llama.cpp manifests", () => {
    expect(isSchedulable(model())).toBe(true);
  });

  it("rejects unvalidated definitions", () => {
    expect(isSchedulable(model({ validation_status: "unverified" }))).toBe(false);
  });

  it("rejects llama.cpp manifests whose source is not a GGUF file", () => {
    expect(
      isSchedulable(
        model({
          manifest: {
            runtime: { provider: "llama-cpp" },
            source: { path: "/srv/yonder/models/qwen/model.safetensors" },
          },
        }),
      ),
    ).toBe(false);
  });

  it("keeps mock providers out of the placement surface", () => {
    expect(
      isSchedulable(
        model({ manifest: { runtime: { provider: "mock" }, source: { path: "" } } }),
      ),
    ).toBe(false);
  });

  it("accepts diffusers manifests without a GGUF path", () => {
    expect(
      isSchedulable(
        model({
          manifest: { runtime: { provider: "diffusers" }, source: { path: "/srv/sdxl" } },
        }),
      ),
    ).toBe(true);
  });
});

describe("isGenerationModel / partitionModelDefinitions", () => {
  it("treats diffusers and image/video profiles as generation", () => {
    expect(
      isGenerationModel(
        model({
          manifest: { runtime: { provider: "diffusers", profile: "image" }, source: {} },
        }),
      ),
    ).toBe(true);
    expect(
      isGenerationModel(
        model({
          id: "vid",
          manifest: { runtime: { provider: "diffusers", profile: "video" }, source: {} },
        }),
      ),
    ).toBe(true);
    expect(isGenerationModel(model())).toBe(false);
  });

  it("puts generation definitions after text ones", () => {
    const chat = model({ id: "chat", display_name: "Chat" });
    const gen = model({
      id: "gen",
      display_name: "Gen",
      capabilities: ["image"],
      manifest: { runtime: { provider: "diffusers", profile: "image" }, source: {} },
    });
    const { textModels, generationModels } = partitionModelDefinitions([gen, chat]);
    expect(textModels.map((item) => item.id)).toEqual(["chat"]);
    expect(generationModels.map((item) => item.id)).toEqual(["gen"]);
  });
});

describe("placement inputs", () => {
  it("converts declared GPU memory to bytes and stays null when absent", () => {
    expect(minimumGpuMemoryBytes(model())).toBe(12 * 1024 ** 3);
    expect(
      minimumGpuMemoryBytes(
        model({ manifest: { runtime: { provider: "llama-cpp" }, source: {} } }),
      ),
    ).toBeNull();
  });

  it("expands the requested index list per placement mode", () => {
    expect(requestedGpuIndexes("single_gpu", 1, 0)).toEqual([1]);
    expect(requestedGpuIndexes("two_gpu", 0, 1)).toEqual([0, 1]);
  });

  it("only counts deployments that still hold a GPU", () => {
    const deployments = [
      deployment({ id: "held", gpu_assignments: [0], status: "ready" }),
      deployment({ id: "gone", gpu_assignments: [1], status: "unloaded" }),
    ];
    expect(occupantsForGpus(deployments, "worker-ai1", [0]).map((item) => item.id)).toEqual(
      ["held"],
    );
    expect(occupantsForGpus(deployments, "worker-ai1", [1])).toEqual([]);
    expect(occupantsForGpus(deployments, "worker-other", [0])).toEqual([]);
  });
});

describe("evaluatePlacement", () => {
  it("clears every safeguard for a free GPU with declared headroom", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node(),
      mode: "single_gpu",
      gpuIndexes: [1],
      deployments: [deployment({ gpu_assignments: [0] })],
    });
    expect(plan.blocked).toBe(false);
    expect(plan.requiresConfirmation).toBe(false);
    expect(check(plan, "gpu_occupancy").level).toBe("ok");
    expect(check(plan, "vram_headroom").level).toBe("ok");
    expect(plan.freeBytes).toBe(15 * 1024 ** 3);
  });

  it("warns and names the existing GPU lane when shared capacity is available", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node(),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [deployment({ id: "chat-lane", gpu_assignments: [0] })],
    });
    expect(plan.blocked).toBe(false);
    expect(plan.requiresConfirmation).toBe(true);
    expect(check(plan, "gpu_occupancy").level).toBe("warn");
    expect(plan.occupantDeploymentIds).toEqual(["chat-lane"]);
    expect(check(plan, "gpu_occupancy").detail).toContain("chat-lane");
  });

  it("blocks placement onto a worker in maintenance", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node({ maintenance: true }),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
    });
    expect(check(plan, "worker").level).toBe("block");
    expect(plan.blocked).toBe(true);
  });

  it("blocks placement onto a disconnected worker", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node({ connected: false, status: "disconnected" }),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
    });
    expect(check(plan, "worker").detail).toContain("worker_disconnected");
    expect(plan.blocked).toBe(true);
  });

  it("blocks a GPU index that is not in the reported inventory", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node(),
      mode: "single_gpu",
      gpuIndexes: [7],
      deployments: [],
    });
    expect(check(plan, "gpu_selection").level).toBe("block");
  });

  it("blocks a two-GPU pool that names the same device twice", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node(),
      mode: "two_gpu",
      gpuIndexes: [1, 1],
      deployments: [],
    });
    expect(check(plan, "gpu_selection").level).toBe("block");
    expect(plan.gpuIndexes).toEqual([1]);
  });

  it("blocks when the worker reports no GPUs at all", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node({
        hardware: { gpu_discovery_status: "not_supported", host: {}, gpus: [] },
      }),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
    });
    expect(check(plan, "gpu_selection").detail).toContain("not_supported");
    expect(plan.blocked).toBe(true);
  });

  it("fails closed on headroom when measured VRAM is unreported", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node({
        hardware: {
          gpu_discovery_status: "available",
          host: {},
          gpus: [
            {
              index: available(0),
              name: available("Tesla V100"),
              memory_used_bytes: { availability: "collection_failed" },
              memory_total_bytes: { availability: "collection_failed" },
            },
          ],
        },
      }),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
    });
    const headroom = check(plan, "vram_headroom");
    expect(headroom.level).toBe("block");
    expect(headroom.detail).toContain("fails closed");
    expect(plan.blocked).toBe(true);
    expect(plan.freeBytes).toBeNull();
  });

  it("fails closed when the selected GPU never reported its index", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node({
        hardware: {
          gpu_discovery_status: "available",
          host: {},
          gpus: [
            {
              ...gpu(0, 1),
              index: { availability: "not_reported" },
            },
          ],
        },
      }),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
    });
    expect(check(plan, "vram_headroom").level).toBe("block");
    expect(plan.blocked).toBe(true);
  });

  it("blocks when measured free VRAM cannot cover the requirement plus reserve", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node({
        hardware: {
          gpu_discovery_status: "available",
          host: {},
          gpus: [gpu(0, 14)],
        },
      }),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
    });
    const headroom = check(plan, "vram_headroom");
    expect(headroom.level).toBe("block");
    expect(headroom.detail).toContain("runtime reserve");
    expect(plan.blocked).toBe(true);
  });

  it("checks the requirement per GPU across a two-GPU pool", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node(),
      mode: "two_gpu",
      gpuIndexes: [0, 1],
      deployments: [],
    });
    expect(plan.freeBytes).toBe(30 * 1024 ** 3);
    expect(plan.requiredBytes).toBe(26 * 1024 ** 3);
    expect(check(plan, "vram_headroom").level).toBe("ok");
  });

  it("blocks a two-GPU pool when only one device has the required headroom", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node({
        hardware: {
          gpu_discovery_status: "available",
          host: {},
          gpus: [gpu(0, 1), gpu(1, 10)],
        },
      }),
      mode: "two_gpu",
      gpuIndexes: [0, 1],
      deployments: [],
    });
    const headroom = check(plan, "vram_headroom");
    expect(headroom.level).toBe("block");
    expect(headroom.detail).toContain("GPU 1");
    expect(headroom.detail).not.toContain("GPU 0");
  });

  it("warns when the manifest declares no minimum GPU memory", () => {
    const plan = evaluatePlacement({
      model: model({
        manifest: {
          runtime: { provider: "llama-cpp" },
          source: { path: "/srv/model.gguf" },
        },
      }),
      node: node(),
      mode: "single_gpu",
      gpuIndexes: [1],
      deployments: [],
    });
    expect(check(plan, "vram_headroom").level).toBe("warn");
  });

  it("blocks when GPU discovery is degraded even though devices are listed", () => {
    const plan = evaluatePlacement({
      model: model(),
      node: node({
        hardware: {
          gpu_discovery_status: "collection_failed",
          gpu_discovery_message: "nvidia-smi exited 9",
          host: {},
          gpus: [gpu(0, 1)],
        },
      }),
      mode: "single_gpu",
      gpuIndexes: [0],
      deployments: [],
    });
    const inventory = check(plan, "gpu_inventory");
    expect(inventory.level).toBe("block");
    expect(inventory.detail).toContain("nvidia-smi exited 9");
    expect(plan.blocked).toBe(true);
  });

  it("blocks unverified models and missing selections", () => {
    const plan = evaluatePlacement({
      model: model({ validation_status: "unverified" }),
      node: null,
      mode: "single_gpu",
      gpuIndexes: [],
      deployments: [],
    });
    expect(check(plan, "model").level).toBe("block");
    expect(check(plan, "worker").level).toBe("block");
    expect(check(plan, "gpu_selection").level).toBe("block");
    expect(plan.blocked).toBe(true);
  });
});
