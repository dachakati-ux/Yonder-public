import { GATEWAY_URL } from "@/lib/brand";
import type { YonderErrorBody } from "@/types";
import { GatewayError } from "@/lib/gateway";

export interface ModelDefinition {
  id: string;
  display_name: string;
  description: string;
  manifest: Record<string, unknown>;
  capabilities: string[];
  validation_status: string;
  validation_issues: Array<Record<string, unknown>>;
}

export interface DeploymentView {
  id: string;
  model_definition_id: string;
  worker_id: string;
  provider: string;
  status: string;
  gpu_assignments: number[];
  runtime_configuration?: Record<string, unknown>;
  load_progress_percent?: number | null;
  loaded_at?: string | null;
  last_successful_load?: string | null;
  last_error?: string | null;
  failure_reason?: string | null;
  request_count: number;
  active_requests: number;
  logs: string[];
}

export interface AliasView {
  alias: string;
  deployment_id: string;
  state: string;
  priority: number;
  experimental: boolean;
}

export interface ModelFolderCandidate {
  path: string;
  folder_name: string;
  id: string;
  display_name: string;
  status: string;
  recognized: boolean;
  modality: string;
  capabilities: string[];
  model_format: string;
  quantization: string;
  runtime_candidate: string;
  suggested_aliases: string[];
  model_class?: string | null;
  file_count: number;
  size_bytes: number;
  issues: Array<{ code: string; message: string; path?: string | null }>;
}

async function parseJson<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    if (body?.error?.code) {
      throw new GatewayError(response.status, body as YonderErrorBody);
    }
    throw new Error(`Gateway request failed (${response.status})`);
  }
  return body as T;
}

export async function fetchModelsAdmin(): Promise<ModelDefinition[]> {
  const response = await fetch(`${GATEWAY_URL}/api/admin/models`);
  const body = await parseJson<{ models: ModelDefinition[] }>(response);
  return body.models;
}

export async function fetchModelRoots(): Promise<
  Array<{ path: string; exists: boolean }>
> {
  const response = await fetch(`${GATEWAY_URL}/api/admin/model-folders/roots`);
  const body = await parseJson<{
    roots: Array<{ path: string; exists: boolean }>;
  }>(response);
  return body.roots;
}

export async function scanModelFolder(path: string): Promise<ModelFolderCandidate[]> {
  const response = await fetch(`${GATEWAY_URL}/api/admin/model-folders/scan`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const body = await parseJson<{ candidates: ModelFolderCandidate[] }>(response);
  return body.candidates;
}

export async function prepareModelFolder(
  path: string,
  force = false,
): Promise<ModelFolderCandidate> {
  const response = await fetch(`${GATEWAY_URL}/api/admin/model-folders/prepare`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path, force }),
  });
  const body = await parseJson<{ candidate: ModelFolderCandidate }>(response);
  return body.candidate;
}

export async function fetchDeployments(): Promise<DeploymentView[]> {
  const response = await fetch(`${GATEWAY_URL}/api/admin/deployments`);
  const body = await parseJson<{ deployments: DeploymentView[] }>(response);
  return body.deployments;
}

export async function fetchRoutes(): Promise<{
  aliases: AliasView[];
  deployments: DeploymentView[];
}> {
  const response = await fetch(`${GATEWAY_URL}/api/admin/routes`);
  return parseJson(response);
}

export async function validateModel(modelId: string) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/models/${modelId}/validate`,
    { method: "POST" },
  );
  return parseJson<{ valid: boolean; issues: Array<Record<string, unknown>> }>(
    response,
  );
}

export async function loadDeployment(input: {
  deployment_id?: string;
  model_definition_id?: string;
  worker_id?: string;
  placement_mode?: "single_gpu" | "two_gpu";
  gpu_indexes?: number[];
  context_length?: number;
}) {
  const response = await fetch(`${GATEWAY_URL}/api/admin/deployments/load`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseJson<{ deployment: DeploymentView }>(response);
}

export async function drainDeployment(deploymentId: string) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/deployments/${deploymentId}/drain`,
    { method: "POST" },
  );
  return parseJson<{ deployment: DeploymentView }>(response);
}

export async function unloadDeployment(deploymentId: string) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/deployments/${deploymentId}/unload`,
    { method: "POST" },
  );
  return parseJson<{ deployment: DeploymentView }>(response);
}

export async function setDeploymentLock(deploymentId: string, locked: boolean) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/deployments/${deploymentId}/lock`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ locked }),
    },
  );
  return parseJson<{ deployment: DeploymentView }>(response);
}

export async function fetchCapacityEvents(limit = 20) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/capacity/events?limit=${limit}`,
  );
  return parseJson<{
    capacity: Record<string, unknown>;
    events: Array<{
      id: string;
      ts: string;
      event_type: string;
      model_definition_id?: string | null;
      deployment_id?: string | null;
      reason?: string | null;
      details?: Record<string, unknown>;
    }>;
  }>(response);
}

export async function retryDeployment(deploymentId: string) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/deployments/${deploymentId}/retry`,
    { method: "POST" },
  );
  return parseJson<{ deployment: DeploymentView }>(response);
}

export async function fetchDeploymentLogs(deploymentId: string) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/deployments/${deploymentId}/logs`,
  );
  return parseJson<{ logs: string[] }>(response);
}

export async function runTestPrompt(modelAlias: string, prompt: string) {
  const response = await fetch(`${GATEWAY_URL}/api/admin/test-prompt`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ model_alias: modelAlias, prompt }),
  });
  return parseJson<{
    response: {
      choices: Array<{ message: { content: string } }>;
    };
  }>(response);
}

export async function runEmbeddingTest(modelAlias: string, input: string) {
  const response = await fetch(`${GATEWAY_URL}/v1/embeddings`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ model: modelAlias, input }),
  });
  return parseJson<{ data: Array<{ embedding: number[] }>; yonder: Record<string, unknown> }>(
    response,
  );
}

export async function runRerankTest(modelAlias: string) {
  const response = await fetch(`${GATEWAY_URL}/v1/rerank`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: modelAlias,
      query: "capital of France",
      documents: ["Paris is the capital of France.", "The sky is blue."],
      top_n: 2,
    }),
  });
  return parseJson<{ results: Array<{ document: string; relevance_score: number }> }>(
    response,
  );
}

export async function runGenerationTest(mode: "txt2img" | "txt2video") {
  const response = await fetch(`${GATEWAY_URL}/v1/generation/jobs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      mode,
      prompt: "Yonder runtime verification canary",
      steps: 1,
    }),
  });
  return parseJson<{ job: { id: string; status: string; availability: string } }>(
    response,
  );
}

export async function repointAlias(alias: string, deploymentId: string) {
  const response = await fetch(`${GATEWAY_URL}/api/admin/routes/${alias}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ deployment_id: deploymentId, enabled: true }),
  });
  return parseJson<{ alias: AliasView }>(response);
}
