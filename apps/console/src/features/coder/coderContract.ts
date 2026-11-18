/** Phase A coder desk contract — read-only assistant, no workspace tools. */

export const CODER_ALIAS = "yonder-coder";

export const CODER_SYSTEM_PROMPT = [
  "You are Yonder Coder, an internal read-only coding assistant.",
  "Trust ceiling: read-only. You cannot access repositories, filesystems, shells, or networks.",
  "Do not claim you executed code, opened files, ran tests, or applied patches.",
  "When suggesting changes, emit unified diffs labeled as PROPOSED ONLY — the human applies them.",
  "If an API, path, or behavior is uncertain, say unknown or not_reported. Never invent readiness.",
  "Refuse to handle secrets, credentials, private keys, or production tokens. Ask the human to redact.",
  "Prefer concrete, reviewable patches over vague advice.",
].join(" ");

export type CoderTrustChip = {
  id: string;
  label: string;
  kind: "ok" | "warn" | "info";
};

/** Session-level trust chips for the Coder desk / Chat binder (never invent READY). */
export function coderSessionTrustChips(opts: {
  aliasAvailability: string | null;
  hasWorkspace: boolean;
}): CoderTrustChip[] {
  const chips: CoderTrustChip[] = [
    { id: "read_only", label: "read_only", kind: "ok" },
  ];
  if (opts.hasWorkspace) {
    chips.push({
      id: "workspace_read",
      label: "workspace: read tools",
      kind: "info",
    });
  } else {
    chips.push({
      id: "no_workspace",
      label: "no_workspace",
      kind: "info",
    });
  }
  const avail = opts.aliasAvailability ?? "not_reported";
  if (avail === "ready") {
    chips.push({ id: "alias_ready", label: "yonder-coder: ready", kind: "ok" });
  } else if (avail === "not_ready") {
    chips.push({
      id: "alias_not_ready",
      label: "yonder-coder: not_ready",
      kind: "warn",
    });
  } else {
    chips.push({
      id: "alias_not_reported",
      label: `yonder-coder: ${avail}`,
      kind: "warn",
    });
  }
  return chips;
}

export function buildCoderChatHref(opts?: {
  prompt?: string;
  hasWorkspace?: boolean;
}): string {
  const params = new URLSearchParams();
  params.set("desk", "coder");
  params.set("model", CODER_ALIAS);
  params.set("system", CODER_SYSTEM_PROMPT);
  params.set("recipe", "code-assist");
  if (opts?.prompt) {
    params.set("prompt", opts.prompt);
  }
  if (opts?.hasWorkspace) {
    params.set("coder_workspace", "1");
  }
  return `/?${params.toString()}`;
}
