import { GATEWAY_URL } from "@/lib/brand";
import type { YonderErrorBody } from "@/types";
import { GatewayError } from "@/lib/gateway";

import type { HardwareNode } from "@/lib/hardware";

export type { HardwareNode, MetricValue } from "@/lib/hardware";
export { formatMetric } from "@/lib/hardware";

export type OperationsSummary = {
  gateway: Record<string, unknown>;
  workers: {
    total: number;
    connected: number;
    disconnected: number;
    maintenance: number;
    items: Array<Record<string, unknown>>;
  };
  routes: Array<Record<string, unknown>>;
  deployments: Array<Record<string, unknown>>;
  active_requests: number;
  queued_requests: number;
  admission?: {
    max_active_requests_per_deployment: number;
    max_queue_depth: number;
    queue_timeout_seconds: number;
    inference_timeout_seconds: number;
    max_prompt_chars: number;
    max_output_tokens: number;
  };
  capacity?: {
    active_requests: number;
    queued_requests: number;
    any_saturated: boolean;
    deployments: Array<{
      deployment_id: string;
      status: string;
      active_requests: number;
      queue_depth: number;
      max_active_requests: number;
      max_queue_depth: number;
      headroom_active: number;
      headroom_queue: number;
      saturated: boolean;
    }>;
  };
  recent_failures: Array<Record<string, unknown>>;
  recent_requests: Array<Record<string, unknown>>;
  providers: Array<Record<string, unknown>>;
  recent_admin_events: Array<Record<string, unknown>>;
};

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

export async function fetchHardware(): Promise<{ nodes: HardwareNode[] }> {
  const response = await fetch(`${GATEWAY_URL}/api/admin/hardware`);
  return parseJson(response);
}

export async function fetchOperationsSummary(): Promise<OperationsSummary> {
  const response = await fetch(`${GATEWAY_URL}/api/admin/operations/summary`);
  return parseJson(response);
}

export async function setWorkerMaintenance(workerId: string, maintenance: boolean) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/workers/${workerId}/maintenance`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ maintenance }),
    },
  );
  return parseJson(response);
}

export async function enableRoute(alias: string, enabled: boolean) {
  const response = await fetch(`${GATEWAY_URL}/api/admin/routes/${alias}/enable`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return parseJson(response);
}

export async function retryProviderHealth(providerName: string) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/providers/${providerName}/health-check`,
    { method: "POST" },
  );
  return parseJson(response);
}

export async function drainDeployment(deploymentId: string) {
  const response = await fetch(
    `${GATEWAY_URL}/api/admin/deployments/${deploymentId}/drain`,
    { method: "POST" },
  );
  return parseJson(response);
}

export async function runOpsTestPrompt(modelAlias: string, prompt: string) {
  const response = await fetch(`${GATEWAY_URL}/api/admin/test-prompt`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ model_alias: modelAlias, prompt }),
  });
  return parseJson(response);
}
