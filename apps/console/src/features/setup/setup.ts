import { GATEWAY_URL } from "@/lib/brand";

export type SetupAlias = {
  alias: string;
  kind: "mock" | "model";
  status: "unavailable" | "route_ready";
  experimental: boolean;
};
export type SetupStatus = {
  security_mode: string;
  gateway_ready: boolean;
  aliases: SetupAlias[];
};

export async function fetchSetup(signal?: AbortSignal): Promise<SetupStatus> {
  const response = await fetch(GATEWAY_URL + "/v1/onboarding/status", {
    cache: "no-store", signal,
  });
  if (!response.ok) throw new Error(
    response.status === 401 || response.status === 403
      ? "Setup requires authorized chat access. Contact your administrator."
      : "Gateway returned HTTP " + response.status + ". Check its URL and logs.",
  );
  return response.json() as Promise<SetupStatus>;
}

export function readyModels(status: SetupStatus): SetupAlias[] {
  return status.gateway_ready
    ? status.aliases.filter((alias) => alias.kind === "model" && alias.status === "route_ready")
    : [];
}
