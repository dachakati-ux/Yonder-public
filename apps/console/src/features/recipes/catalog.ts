/** Ops-shaped recipe catalog for the Recipes gallery. */

export type RecipeModality =
  | "chat"
  | "rag"
  | "structured"
  | "embed_rerank"
  | "code";

export type RecipeReadiness = "ready" | "not_ready" | "not_supported" | "unknown";

export interface Recipe {
  id: string;
  title: string;
  problem: string;
  modality: RecipeModality;
  /** Preferred model alias when ready. */
  alias: string;
  /** Optional fallback aliases to check for readiness. */
  aliasFallbacks?: string[];
  samplePrompt: string;
  systemPrompt?: string;
  guidedMode?: "summarize" | "critique" | "explain" | "coach";
  labHref?: string;
  playgroundHref?: string;
  curlPath: "/v1/chat/completions" | "/v1/embeddings" | "/v1/rerank" | "/v1/lab/run";
  curlBody: Record<string, unknown>;
  wave: number;
}

export const RECIPES: Recipe[] = [
  {
    id: "ticket-triage",
    title: "Ticket / email triage",
    problem:
      "Turn a messy inbound ticket into urgency, summary, and a suggested owner.",
    modality: "chat",
    alias: "yonder-chat",
    aliasFallbacks: ["yonder-nemotron-4b", "yonder-qwen35-9b"],
    samplePrompt:
      "From: noc@example.com\nSubject: VPN flapping on ai3\n\n" +
      "Users on the lab VPN keep dropping for 30–90s. Started after the Friday " +
      "firewall change. Please triage urgency and who should own this.",
    systemPrompt:
      "You are an operations triage assistant. Reply with: (1) one-line summary, " +
      "(2) urgency low|medium|high|critical, (3) suggested owner role, " +
      "(4) first three investigation steps. Do not invent outage facts.",
    labHref: "/lab/guided",
    guidedMode: "summarize",
    curlPath: "/v1/chat/completions",
    curlBody: {
      model: "yonder-chat",
      messages: [
        {
          role: "system",
          content:
            "You are an operations triage assistant. Reply with summary, urgency, owner, steps.",
        },
        {
          role: "user",
          content: "Triage this ticket: VPN flapping on ai3 after firewall change.",
        },
      ],
      stream: false,
      max_tokens: 400,
    },
    wave: 1,
  },
  {
    id: "policy-qa",
    title: "Policy / runbook Q&A",
    problem:
      "Answer change-window and access questions grounded in trusted runbook chunks.",
    modality: "rag",
    alias: "yonder-chat",
    samplePrompt: "When are production change windows allowed, and who must approve?",
    labHref: "/lab/rag",
    curlPath: "/v1/lab/run",
    curlBody: {
      ability: "rag",
      input: "When are production change windows allowed, and who must approve?",
    },
    wave: 1,
  },
  {
    id: "intake-grade",
    title: "Structured intake grade",
    problem:
      "Score an intake submission as schema-valid JSON with pass/fail criteria.",
    modality: "structured",
    alias: "yonder-chat",
    samplePrompt:
      "Intake: New service wants GPU access for overnight batch jobs. " +
      "They listed a contact and rollback plan but omitted capacity estimate.",
    labHref: "/lab/structured",
    curlPath: "/v1/lab/run",
    curlBody: {
      ability: "structured",
      input:
        "Grade this intake: contact + rollback present; capacity estimate missing.",
    },
    wave: 1,
  },
  {
    id: "doc-search-rerank",
    title: "Doc search + rerank",
    problem:
      "Embed candidate docs then rerank so the most relevant runbook rises to the top.",
    modality: "embed_rerank",
    alias: "yonder-rerank-4b",
    aliasFallbacks: ["yonder-embed-4b", "yonder-embed-8b"],
    samplePrompt: "GPU placement and context length limits for V100 workers",
    playgroundHref: "/lab/rerank",
    curlPath: "/v1/rerank",
    curlBody: {
      model: "yonder-rerank-4b",
      query: "GPU placement and context length limits for V100 workers",
      documents: [
        "Yonder placement_limits clamp context by GPU count and VRAM headroom.",
        "The cafeteria menu includes salad and soup.",
        "Workers register with the control plane; clients use stable aliases.",
      ],
      top_n: 3,
    },
    wave: 2,
  },
  {
    id: "code-assist",
    title: "Code / config assist",
    problem:
      "Ask the coder alias for a safe systemd or YAML change without inventing hosts.",
    modality: "code",
    alias: "yonder-coder",
    samplePrompt:
      "Draft a systemd drop-in that adds ReadOnlyPaths for /opt/cuda-12.6 under " +
      "ProtectSystem=strict. Explain each line. Do not invent hostnames.",
    curlPath: "/v1/chat/completions",
    curlBody: {
      model: "yonder-coder",
      messages: [
        {
          role: "user",
          content:
            "Draft a systemd drop-in adding ReadOnlyPaths for /opt/cuda-12.6.",
        },
      ],
      stream: false,
      max_tokens: 600,
    },
    wave: 1,
  },
  {
    id: "meeting-actions",
    title: "Meeting notes → actions",
    problem:
      "Turn raw notes into decisions and owners without inventing attendees.",
    modality: "chat",
    alias: "yonder-chat",
    samplePrompt:
      "Notes: Discussed NFS model root on ai1; ai3/ai7 mounts look healthy. " +
      "Need measured context caps before promoting manifests. Alex owns review. " +
      "Defer fleet stress until tomorrow.",
    systemPrompt:
      "Extract decisions and action items as bullets with owners when stated. " +
      "Do not invent people or deadlines.",
    labHref: "/lab/guided",
    guidedMode: "summarize",
    curlPath: "/v1/chat/completions",
    curlBody: {
      model: "yonder-chat",
      messages: [
        {
          role: "system",
          content: "Extract decisions and action items. Do not invent owners.",
        },
        {
          role: "user",
          content: "Meeting notes about NFS mounts and context-cap review.",
        },
      ],
      stream: false,
      max_tokens: 400,
    },
    wave: 1,
  },
  {
    id: "sel-triage",
    title: "SEL triage",
    problem:
      "Classify a sanitized SEL excerpt into severity, likely FRU, and next action.",
    modality: "chat",
    alias: "yonder-chat",
    samplePrompt:
      "SEL fixture lab-host-a:\n" +
      "2026-07-10T03:11:02Z | critical | Memory | Uncorrectable ECC on DIMM A1\n" +
      "2026-07-10T03:11:05Z | warning | Memory | Correctable ECC threshold exceeded\n\n" +
      "Triage severity, likely FRU, evidence quote, and next action. Do not invent extra SEL lines.",
    systemPrompt:
      "You are a Site Ops triage assistant. Reply with severity, likely FRU, " +
      "evidence quote, and next action. Never invent SEL events or serials.",
    labHref: "/lab/guided",
    guidedMode: "summarize",
    curlPath: "/v1/chat/completions",
    curlBody: {
      model: "yonder-chat",
      messages: [
        {
          role: "system",
          content:
            "Site Ops SEL triage. Severity, FRU, evidence, next action. No invented events.",
        },
        {
          role: "user",
          content: "Triage Uncorrectable ECC on DIMM A1 from fixture SEL.",
        },
      ],
      stream: false,
      max_tokens: 400,
    },
    wave: 2,
  },
  {
    id: "dc-incident-structured",
    title: "DC incident structured intake",
    problem:
      "Turn messy Site Ops notes into a fail-closed incident intake schema.",
    modality: "structured",
    alias: "yonder-chat",
    samplePrompt:
      "Notes: lab-host-a threw uncorrectable ECC on DIMM A1 per SEL. Health sweep " +
      "marks memory bad; GPU section not_reported. Open incident for Site Ops.",
    systemPrompt:
      "Emit JSON matching yonder.siteops.incident_intake.v1. Use only stated evidence. " +
      "Do not invent host ids or serials.",
    labHref: "/lab/structured",
    curlPath: "/v1/lab/run",
    curlBody: {
      ability: "structured",
      input:
        "Grade a Site Ops incident intake that uses host lab-host-a and cites SEL DIMM A1.",
    },
    wave: 2,
  },
  {
    id: "rma-packet",
    title: "RMA packet",
    problem:
      "Draft a structured RMA packet from known failure evidence — no fake vendor APIs.",
    modality: "chat",
    alias: "yonder-chat",
    samplePrompt:
      "Build an RMA packet for lab-host-a DIMM A1 after uncorrectable ECC. " +
      "warranty_end is not_reported. List required fields only from evidence.",
    systemPrompt:
      "Produce an RMA-oriented summary with host_id, part_number, failure_summary, " +
      "evidence_refs. Use not_reported for unknown warranty_end. No invented serials.",
    labHref: "/lab/guided",
    guidedMode: "summarize",
    curlPath: "/v1/chat/completions",
    curlBody: {
      model: "yonder-chat",
      messages: [
        {
          role: "system",
          content: "Draft RMA fields from evidence only. Never invent warranty dates.",
        },
        {
          role: "user",
          content: "RMA packet for lab-host-a DIMM A1 uncorrectable ECC.",
        },
      ],
      stream: false,
      max_tokens: 400,
    },
    wave: 2,
  },
  {
    id: "inventory-qa",
    title: "Inventory Q&A",
    problem:
      "Answer inventory questions only from teaching inventory; refuse invention.",
    modality: "rag",
    alias: "yonder-chat",
    samplePrompt: "What NIC is installed on lab-host-a according to inventory?",
    labHref: "/lab/rag",
    playgroundHref: "/academy/sandbox",
    curlPath: "/v1/lab/run",
    curlBody: {
      ability: "rag",
      input: "What NIC is installed on lab-host-a according to inventory?",
    },
    wave: 2,
  },
];

export function recipeById(id: string): Recipe | undefined {
  return RECIPES.find((r) => r.id === id);
}

export function buildChatHref(recipe: Recipe): string {
  const params = new URLSearchParams();
  params.set("recipe", recipe.id);
  params.set("prompt", recipe.samplePrompt);
  params.set("model", recipe.alias);
  if (recipe.systemPrompt) {
    params.set("system", recipe.systemPrompt);
  }
  return `/?${params.toString()}`;
}

export function buildCurl(recipe: Recipe, gatewayUrl: string): string {
  const body = JSON.stringify(recipe.curlBody, null, 2);
  return (
    `curl -sS -X POST '${gatewayUrl}${recipe.curlPath}' \\\n` +
    `  -H 'content-type: application/json' \\\n` +
    `  -d '${body.replace(/'/g, "'\\''")}'`
  );
}

/** Resolve readiness from admin routes or /v1/models ids. */
export function resolveRecipeReadiness(
  recipe: Recipe,
  opts: {
    readyAliases: Set<string>;
    listedModels: Set<string>;
  },
): RecipeReadiness {
  const { readyAliases, listedModels } = opts;
  if (recipe.modality === "embed_rerank" && readyAliases.size === 0 && listedModels.size === 0) {
    return "unknown";
  }
  const candidates = [recipe.alias, ...(recipe.aliasFallbacks ?? [])];
  for (const alias of candidates) {
    if (readyAliases.has(alias) || listedModels.has(alias)) {
      return "ready";
    }
  }
  // Code assist soft-disables when coder missing.
  if (recipe.modality === "code") {
    return "not_ready";
  }
  return readyAliases.size + listedModels.size > 0 ? "not_ready" : "unknown";
}
