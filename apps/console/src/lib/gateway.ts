import type {
  Conversation,
  ConversationSummary,
  ModelCard,
  YonderErrorBody,
} from "@/types";
import { GATEWAY_URL } from "@/lib/brand";
import { extractSsePayloads, parseSsePayload } from "@/lib/sse";

export class GatewayError extends Error {
  code: string;
  requestId: string;
  status: number;
  details?: Record<string, unknown>;

  constructor(status: number, body: YonderErrorBody) {
    super(body.error.message);
    this.name = "GatewayError";
    this.status = status;
    this.code = body.error.code;
    this.requestId = body.error.request_id;
    this.details = body.error.details;
  }
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

export async function listModels(): Promise<ModelCard[]> {
  const response = await fetch(`${GATEWAY_URL}/v1/models`);
  const body = await parseJson<{ data: ModelCard[] }>(response);
  return body.data;
}

export interface SpeechStatus {
  enabled: boolean;
  availability: string;
  message?: string | null;
  stt_alias?: string;
  tts_alias?: string;
  worker?: Record<string, unknown>;
}

export async function getSpeechStatus(): Promise<SpeechStatus> {
  const response = await fetch(`${GATEWAY_URL}/v1/speech/status`);
  return parseJson<SpeechStatus>(response);
}

export async function transcribeSpeech(file: Blob, filename = "audio.webm"): Promise<{
  text: string;
  availability: string;
  language?: string | null;
  message?: string | null;
}> {
  const form = new FormData();
  form.append("file", file, filename);
  const response = await fetch(`${GATEWAY_URL}/v1/speech/transcribe`, {
    method: "POST",
    body: form,
  });
  return parseJson(response);
}

export async function synthesizeSpeech(text: string, voice?: string): Promise<{
  availability: string;
  mime_type: string;
  artifact_id: string | null;
  url: string | null;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/speech/synthesize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice }),
  });
  return parseJson(response);
}

export function speechAudioUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${GATEWAY_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export interface GlossaryTermPayload {
  id: string;
  term: string;
  aliases: string[];
  short: string;
  body: string;
  related: string[];
  links: { label: string; href: string }[];
}

export interface GlossaryResponse {
  source: string;
  path: string;
  terms: GlossaryTermPayload[];
  count: number;
}

/** Authored help glossary from Gateway (config/help/glossary.yaml). */
export async function fetchGlossary(): Promise<GlossaryResponse> {
  const response = await fetch(`${GATEWAY_URL}/v1/help/glossary`);
  return parseJson<GlossaryResponse>(response);
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await fetch(`${GATEWAY_URL}/api/chat/conversations`);
  const body = await parseJson<{ conversations: ConversationSummary[] }>(response);
  return body.conversations;
}

export async function createConversation(input: {
  title?: string;
  model_alias: string;
}): Promise<Conversation> {
  const response = await fetch(`${GATEWAY_URL}/api/chat/conversations`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  const body = await parseJson<{ conversation: Conversation }>(response);
  return body.conversation;
}

export async function getConversation(id: string): Promise<Conversation> {
  const response = await fetch(`${GATEWAY_URL}/api/chat/conversations/${id}`);
  const body = await parseJson<{ conversation: Conversation }>(response);
  return body.conversation;
}

export async function renameConversation(
  id: string,
  title: string,
): Promise<Conversation> {
  const response = await fetch(`${GATEWAY_URL}/api/chat/conversations/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title }),
  });
  const body = await parseJson<{ conversation: Conversation }>(response);
  return body.conversation;
}

export async function deleteConversation(id: string): Promise<void> {
  const response = await fetch(`${GATEWAY_URL}/api/chat/conversations/${id}`, {
    method: "DELETE",
  });
  await parseJson<{ deleted: boolean }>(response);
}

export async function appendMessage(
  conversationId: string,
  input: {
    role: "user" | "assistant" | "system";
    content: string;
    request_id?: string | null;
    metadata?: Record<string, unknown> | null;
  },
): Promise<void> {
  const response = await fetch(
    `${GATEWAY_URL}/api/chat/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  await parseJson(response);
}

export async function replaceAssistantMessage(
  conversationId: string,
  input: {
    content: string;
    request_id?: string | null;
    metadata?: Record<string, unknown> | null;
  },
): Promise<void> {
  const response = await fetch(
    `${GATEWAY_URL}/api/chat/conversations/${conversationId}/messages/replace-assistant`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  await parseJson(response);
}

export async function streamChatCompletion(input: {
  model: string;
  messages: Array<{ role: "system" | "user" | "assistant"; content: string }>;
  conversationId?: string | null;
  signal?: AbortSignal;
  onToken: (token: string) => void;
  onRequestId?: (requestId: string) => void;
  onContext?: (event: import("@/lib/contextMeter").ContextEvent) => void;
}): Promise<string> {
  const response = await fetch(`${GATEWAY_URL}/v1/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: input.model,
      messages: input.messages,
      stream: true,
      ...(input.conversationId
        ? { conversation_id: input.conversationId }
        : {}),
    }),
    signal: input.signal,
  });

  const requestId = response.headers.get("X-Yonder-Request-Id");
  if (requestId && input.onRequestId) {
    input.onRequestId(requestId);
  }

  if (!response.ok) {
    try {
      const body = (await response.json()) as YonderErrorBody;
      if (body?.error?.code) {
        throw new GatewayError(response.status, body);
      }
    } catch (error) {
      if (error instanceof GatewayError) {
        throw error;
      }
    }
    throw new Error(`Chat request failed (${response.status})`);
  }

  if (!response.body) {
    throw new Error("Gateway returned an empty stream body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const extracted = extractSsePayloads(buffer);
    buffer = extracted.remainder;

    for (const payload of extracted.payloads) {
      const parsed = parseSsePayload(payload);
      if (parsed.kind === "done") {
        return full;
      }
      if (parsed.kind === "error") {
        throw new GatewayError(502, { error: parsed.error });
      }
      if (parsed.kind === "context") {
        input.onContext?.(parsed.event);
        continue;
      }
      if (parsed.kind === "token") {
        full += parsed.token;
        input.onToken(parsed.token);
      }
    }
  }

  return full;
}

export async function listLabAbilities(): Promise<{
  abilities: import("@/types").LabAbilityCard[];
  mode: string;
  note: string;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/abilities`);
  return parseJson(response);
}

export async function runLab(input: {
  ability: import("@/types").LabAbilityId;
  input: string;
  guided_mode?: import("@/types").LabGuidedMode;
  media_kind?: import("@/types").LabMediaKind;
  generation?: import("@/types").LabGenerationOptions;
}): Promise<import("@/types").LabRunResult> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/run`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseJson(response);
}

export async function listGenerationPresets(): Promise<{
  modes: string[];
  media_kinds: string[];
  aspect_ratios: string[];
  style_presets: Array<{ id: string; prompt_hint: string }>;
  note: string;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/generation/presets`);
  return parseJson(response);
}

export type AcademyLessonProgress = {
  status: "not_started" | "in_progress" | "completed";
  check_passed: boolean;
  try_it_recorded: boolean;
  check_attempts: number;
  completed_at?: string | null;
};

export type AcademyLesson = {
  id: string;
  track: string;
  title: string;
  summary: string;
  duration_minutes: number;
  objectives: string[];
  sections: Array<{ title: string; body: string }>;
  glossary_term_ids: string[];
  try_it: {
    kind: string;
    label: string;
    href?: string | null;
    ability?: string | null;
    recipe_id?: string | null;
    chat_prompt?: string | null;
    sandbox_preset?: string | null;
  } | null;
  check: {
    kind: "quiz" | "eval_case";
    prompt: string;
    options?: Array<{ id: string; label: string }>;
  };
  completion: string;
  prerequisites: string[];
  next_lesson_id: string | null;
  progress?: AcademyLessonProgress;
};

export type AcademyTrack = {
  id: string;
  title: string;
  summary: string;
  audience: string;
  lesson_ids: string[];
  lessons: AcademyLesson[];
  completion: { total: number; completed: number; percent: number };
};

export type AcademyCurriculum = {
  version: number;
  tracks: AcademyTrack[];
  sandbox: Record<string, number>;
  principal_subject: string;
  progress: Record<string, AcademyLessonProgress>;
};

export async function getAcademyCurriculum(): Promise<AcademyCurriculum> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/curriculum`);
  return parseJson(response);
}

export async function postLessonEvent(
  lessonId: string,
  body: {
    event: "start" | "check" | "try_it" | "complete";
    selected_option_id?: string;
    answer?: string;
    request_id?: string;
  },
): Promise<{
  lesson_id: string;
  status: string;
  check_passed: boolean;
  try_it_recorded: boolean;
  check_correct: boolean | null;
  explanation: string | null;
  completed: boolean;
  next_lesson_id: string | null;
}> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/progress/${encodeURIComponent(lessonId)}/events`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return parseJson(response);
}

export async function resetAcademyProgress(): Promise<{
  principal_subject: string;
  cleared: number;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/progress/reset`, {
    method: "POST",
  });
  return parseJson(response);
}

export type PracticeSandbox = {
  id: string;
  principal_subject: string;
  label: string;
  preset: string | null;
  documents: Array<{ id: string; title: string; text: string; created_at: string }>;
  parts: Array<{
    id: string;
    part_number: string;
    quantity: number;
    incident_id: string;
    note: string;
    created_at: string;
  }>;
  retrieve_runs: number;
  created_at: string;
  updated_at: string;
};

export async function createSandbox(input: {
  label?: string;
  preset?: string | null;
}): Promise<{ sandbox: PracticeSandbox }> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/sandbox`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseJson(response);
}

export async function listSandboxes(): Promise<{ sandboxes: PracticeSandbox[] }> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/sandbox`);
  return parseJson(response);
}

export async function addSandboxDocument(
  sandboxId: string,
  input: { title: string; text: string },
): Promise<{ document: { id: string; title: string }; sandbox: PracticeSandbox }> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/sandbox/${encodeURIComponent(sandboxId)}/documents`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return parseJson(response);
}

export async function retrieveSandbox(
  sandboxId: string,
  input: { query: string; k?: number },
): Promise<{
  hits: Array<{
    document_id: string;
    title: string;
    score: number;
    snippet: string;
    citation: { document_id: string; title: string };
  }>;
  note: string;
  retrieve_runs: number;
}> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/sandbox/${encodeURIComponent(sandboxId)}/retrieve`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return parseJson(response);
}

export async function addSandboxPart(
  sandboxId: string,
  input: {
    part_number: string;
    quantity: number;
    incident_id: string;
    note?: string;
  },
): Promise<{ entry: PracticeSandbox["parts"][number]; sandbox: PracticeSandbox }> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/sandbox/${encodeURIComponent(sandboxId)}/parts`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return parseJson(response);
}

export async function resetSandbox(
  sandboxId: string,
): Promise<{ sandbox: PracticeSandbox }> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/sandbox/${encodeURIComponent(sandboxId)}/reset`,
    { method: "POST" },
  );
  return parseJson(response);
}

export async function listSandboxPresets(): Promise<{
  presets: Array<{ id: string; label: string }>;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/sandbox/presets`);
  return parseJson(response);
}

export type AcademyMissionSummary = {
  id: string;
  title: string;
  track: string;
  summary: string;
  duration_minutes: number;
  honesty: string[];
  step_count: number;
  progress?: {
    status: string;
    completed_steps: string[];
    check_passed: boolean;
  } | null;
};

export type AcademyMission = {
  id: string;
  title: string;
  track: string;
  summary: string;
  duration_minutes: number;
  honesty: string[];
  steps: Array<{
    id: string;
    title: string;
    kind: string;
    instruction: string;
    href?: string | null;
    recipe_id?: string | null;
    ability?: string | null;
    sandbox_preset?: string | null;
  }>;
  completion_check: { kind: string; prompt: string };
};

export async function listMissions(): Promise<{
  missions: AcademyMissionSummary[];
  principal_subject: string;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/missions`);
  return parseJson(response);
}

export async function getMission(missionId: string): Promise<{
  mission: AcademyMission;
  progress: {
    status: string;
    completed_steps: string[];
    check_passed: boolean;
  } | null;
}> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/missions/${encodeURIComponent(missionId)}`,
  );
  return parseJson(response);
}

export async function postMissionEvent(
  missionId: string,
  body: { event: "start" | "step" | "check"; step_id?: string; answer?: string },
): Promise<{
  status: string;
  completed_steps: string[];
  check_passed: boolean;
  check_correct: boolean | null;
  explanation: string | null;
}> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/missions/${encodeURIComponent(missionId)}/events`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return parseJson(response);
}

export type PromptPattern = {
  id: string;
  track: string;
  title: string;
  status: string;
  system: string;
  user_template: string;
  links: Record<string, string>;
};

export async function listPromptPatterns(): Promise<{
  version: number;
  patterns: PromptPattern[];
  note: string;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/prompts`);
  return parseJson(response);
}

export type AcademyChallenge = {
  id: string;
  title: string;
  summary: string;
  track_hint: string;
  sandbox_preset?: string | null;
  mission_id?: string | null;
  criteria: Array<{ id: string; label: string }>;
  completion_prompt: string;
  submission?: {
    passed: boolean;
    criteria_met: string[];
    criteria_missed: string[];
    submitted_at: string;
  } | null;
};

export type AcademyCohort = {
  id: string;
  title: string;
  summary: string;
  starts_at: string;
  ends_at: string;
  challenge_ids: string[];
  challenges: AcademyChallenge[];
};

export async function listCohorts(): Promise<{
  version: number;
  cohorts: AcademyCohort[];
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/cohorts`);
  return parseJson(response);
}

export async function submitChallenge(
  challengeId: string,
  answer: string,
): Promise<{
  passed: boolean;
  criteria_met: string[];
  criteria_missed: string[];
  explanation: string;
  note: string;
}> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/cohorts/challenges/${encodeURIComponent(challengeId)}/submit`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ answer }),
    },
  );
  return parseJson(response);
}

export async function getChallengeBoard(challengeId: string): Promise<{
  challenge_id: string;
  completions: Array<{
    principal_subject: string;
    passed: boolean;
    criteria_met: string[];
    submitted_at: string;
  }>;
  note: string;
}> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/cohorts/challenges/${encodeURIComponent(challengeId)}/board`,
  );
  return parseJson(response);
}

export type InstructorSummary = {
  principals_seen: number;
  tracks: Array<{
    track_id: string;
    title: string;
    learners_completed: number;
    learners_in_progress: number;
    learners_seen: number;
    lesson_total: number;
    lessons?: Array<{
      lesson_id: string;
      completed_count: number;
      in_progress_count: number;
      stalled_count: number;
    }>;
  }>;
  quarantined_corpora: string[];
  challenge_completions: Record<string, number>;
  stall_days?: number;
  class_runner?: {
    recommended_mission_id: string;
    recommended_cohort_id: string;
    join_urls: Record<string, string>;
    note: string;
  };
  note: string;
};

export async function getInstructorSummary(): Promise<InstructorSummary> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/instructor/summary`);
  return parseJson(response);
}

export async function instructorResetProgress(
  principalSubject: string,
): Promise<{ principal_subject: string; cleared: number; reset_by: string }> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/instructor/progress/reset`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ principal_subject: principalSubject }),
  });
  return parseJson(response);
}

export async function instructorCorpusAction(input: {
  corpus_id: string;
  action: "quarantine" | "release";
}): Promise<{ corpus_id: string; action: string; quarantined: string[] }> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/instructor/corpus`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseJson(response);
}

export type WorkbenchNote = {
  id: string;
  title: string;
  body: string;
  tags: string[];
  source: string;
  created_at: string;
  updated_at: string;
};

export async function listWorkbenchNotes(): Promise<{ notes: WorkbenchNote[] }> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/workbench`);
  return parseJson(response);
}

export async function createWorkbenchNote(input: {
  title: string;
  body: string;
  tags?: string[];
  source?: string;
}): Promise<{ note: WorkbenchNote }> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/workbench/notes`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseJson(response);
}

export async function patchWorkbenchNote(
  noteId: string,
  input: {
    title?: string;
    body?: string;
    tags?: string[];
    source?: string;
  },
): Promise<{ note: WorkbenchNote }> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/workbench/notes/${encodeURIComponent(noteId)}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return parseJson(response);
}

export async function deleteWorkbenchNote(
  noteId: string,
): Promise<{ deleted: string }> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/lab/workbench/notes/${encodeURIComponent(noteId)}`,
    { method: "DELETE" },
  );
  return parseJson(response);
}

export async function exportWorkbench(): Promise<{
  principal_subject: string;
  exported_at: string;
  notes: WorkbenchNote[];
  note: string;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/workbench/export`);
  return parseJson(response);
}

export type CapacitySnapshot = {
  lab_runtime: {
    chat_alias: string | null;
    chat_alias_ready: boolean;
    generation_enabled: boolean;
  };
  aliases: Array<{
    alias: string;
    present_in_routes: boolean;
    ready: boolean;
    availability: string;
  }>;
  cpu_vs_gpu: { cpu_first: string[]; gpu_for: string[] };
  note: string;
};

export async function getCapacitySnapshot(): Promise<CapacitySnapshot> {
  const response = await fetch(`${GATEWAY_URL}/v1/lab/capacity`);
  return parseJson(response);
}

export type CoderStatus = {
  request_id?: string;
  enabled: boolean;
  phase: string;
  alias?: string;
  trust_ceiling?: string;
  write_unlock: boolean;
  tools?: string[];
  cage_root?: string;
  grantable_roots?: string[];
  check_allowlist?: string[];
  pipeline_stages?: string[];
  pipeline?: boolean;
  agent_mode?: string;
  note?: string;
  availability?: string;
  message?: string;
};

export type CoderWorkspace = {
  id: string;
  kind: string;
  root_display: string;
  label?: string;
  created_at?: string;
  read_only?: boolean;
  source?: string | null;
  git_head?: string | null;
  remote_url?: string | null;
};

export type CoderRun = {
  id: string;
  workspace_id: string;
  task: string;
  stage: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  check_command?: string;
  artifacts?: Record<string, unknown>;
  timeline?: Array<{ ts?: string; stage: string; result: string; message: string }>;
  verdicts?: Array<{ agent?: string; verdict: string; reasons?: string[] }>;
  push_result?: Record<string, unknown> | null;
  error?: string | null;
  dual_approved?: boolean;
};

export async function coderStatus(): Promise<CoderStatus> {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/status`);
  return parseJson(response);
}

export async function coderListWorkspaces(): Promise<{ workspaces: CoderWorkspace[] }> {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/workspaces`);
  return parseJson(response);
}

export async function coderCreateWorkspace(label = ""): Promise<{ workspace: CoderWorkspace }> {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/workspaces`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ label }),
  });
  return parseJson(response);
}

export async function coderGrantWorkspace(
  path: string,
  label = "",
): Promise<{ workspace: CoderWorkspace }> {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/workspaces/grant`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path, label }),
  });
  return parseJson(response);
}

export async function coderCloneWorkspace(
  source: string,
  label = "",
  ref?: string,
): Promise<{ workspace: CoderWorkspace }> {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/workspaces/clone`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ source, label, ref: ref || null }),
  });
  return parseJson(response);
}

export async function coderRevokeWorkspace(workspaceId: string): Promise<void> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/coder/workspaces/${encodeURIComponent(workspaceId)}`,
    { method: "DELETE" },
  );
  await parseJson(response);
}

export async function coderListDir(workspaceId: string, path = ".") {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/tools/list_dir`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ workspace_id: workspaceId, path }),
  });
  return parseJson(response);
}

export async function coderReadFile(workspaceId: string, path: string) {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/tools/read_file`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ workspace_id: workspaceId, path }),
  });
  return parseJson(response);
}

export async function coderSearch(workspaceId: string, query: string, path = ".") {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/tools/search`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ workspace_id: workspaceId, query, path }),
  });
  return parseJson(response);
}

export async function coderListRuns(): Promise<{ runs: CoderRun[] }> {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/runs`);
  return parseJson(response);
}

export async function coderCreateRun(
  workspaceId: string,
  task: string,
  checkCommand = "python -m pytest",
): Promise<{ run: CoderRun }> {
  const response = await fetch(`${GATEWAY_URL}/v1/coder/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      workspace_id: workspaceId,
      task,
      check_command: checkCommand,
    }),
  });
  return parseJson(response);
}

export async function coderGetRun(runId: string): Promise<{ run: CoderRun }> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/coder/runs/${encodeURIComponent(runId)}`,
  );
  return parseJson(response);
}

export async function coderAdvanceRun(
  runId: string,
  auto = false,
): Promise<{ run: CoderRun }> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/coder/runs/${encodeURIComponent(runId)}/advance`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ auto }),
    },
  );
  return parseJson(response);
}

export async function coderCancelRun(
  runId: string,
): Promise<{ run: CoderRun }> {
  const response = await fetch(
    `${GATEWAY_URL}/v1/coder/runs/${encodeURIComponent(runId)}/cancel`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    },
  );
  return parseJson(response);
}

export type GenerationJob = {
  id: string;
  request_id: string;
  status: string;
  mode: string;
  model_alias: string;
  provider: string;
  availability: string;
  prompt: string;
  progress: Array<{ step: string; message: string; percent?: number | null }>;
  assets: Array<{ artifact_id: string; mime_type: string; url: string; kind: string }>;
  error?: string | null;
  params?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
};

export async function createGenerationJob(input: {
  mode: import("@/types").LabGenerationMode;
  prompt: string;
  negative_prompt?: string;
  aspect_ratio?: string;
  style_preset?: string | null;
  steps?: number;
  guidance?: number;
  strength?: number | null;
  seed?: number | null;
  reference_artifact_ids?: string[];
  model?: string | null;
}): Promise<GenerationJob> {
  const response = await fetch(`${GATEWAY_URL}/v1/generation/jobs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  const body = await parseJson<{ job: GenerationJob }>(response);
  return body.job;
}

export async function getGenerationJob(jobId: string): Promise<GenerationJob> {
  const response = await fetch(`${GATEWAY_URL}/v1/generation/jobs/${jobId}`);
  const body = await parseJson<{ job: GenerationJob }>(response);
  return body.job;
}

export async function waitForGenerationJob(
  jobId: string,
  options?: {
    signal?: AbortSignal;
    intervalMs?: number;
    maxAttempts?: number;
    onUpdate?: (job: GenerationJob) => void;
  },
): Promise<GenerationJob> {
  const intervalMs = options?.intervalMs ?? 1500;
  const maxAttempts = options?.maxAttempts ?? 120;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (options?.signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    const job = await getGenerationJob(jobId);
    options?.onUpdate?.(job);
    if (
      ["succeeded", "completed", "failed", "error", "cancelled", "rejected"].includes(
        job.status,
      )
    ) {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Generation job ${jobId} timed out`);
}

export async function listGenerationJobs(): Promise<GenerationJob[]> {
  const response = await fetch(`${GATEWAY_URL}/v1/generation/jobs`);
  const body = await parseJson<{ jobs: GenerationJob[] }>(response);
  return body.jobs;
}

export async function deleteGenerationJob(jobId: string): Promise<void> {
  const response = await fetch(`${GATEWAY_URL}/v1/generation/jobs/${jobId}`, {
    method: "DELETE",
  });
  await parseJson<{ deleted: boolean; job_id: string }>(response);
}

export async function uploadGenerationReference(
  file: File,
): Promise<{ id: string; url: string; mime_type: string; filename: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", "reference");
  const response = await fetch(`${GATEWAY_URL}/v1/generation/uploads`, {
    method: "POST",
    body: form,
  });
  const body = await parseJson<{
    artifact: { id: string; url: string; mime_type: string; filename: string };
  }>(response);
  return body.artifact;
}

export async function createEmbedding(input: {
  model: string;
  input: string | string[];
}): Promise<{
  model: string;
  data: Array<{ embedding: number[]; index: number }>;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/embeddings`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseJson(response);
}

export async function createRerank(input: {
  model: string;
  query: string;
  documents: string[];
  top_n?: number;
}): Promise<{
  model: string;
  results: Array<{ index: number; relevance_score: number; document?: string }>;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/rerank`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseJson(response);
}

/** Non-streaming chat completion for compare / eval playgrounds. */
export async function completeChat(input: {
  model: string;
  messages: Array<{ role: "system" | "user" | "assistant"; content: string }>;
  max_tokens?: number;
}): Promise<{ content: string; requestId: string | null; durationMs: number }> {
  const started = performance.now();
  const response = await fetch(`${GATEWAY_URL}/v1/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: input.model,
      messages: input.messages,
      stream: false,
      max_tokens: input.max_tokens ?? 400,
    }),
  });
  const requestId = response.headers.get("X-Yonder-Request-Id");
  const body = await parseJson<{
    choices?: Array<{ message?: { content?: string } }>;
  }>(response);
  const content = body.choices?.[0]?.message?.content ?? "";
  return {
    content,
    requestId,
    durationMs: performance.now() - started,
  };
}

export interface AuthProviderTemplate {
  label: string;
  scopes: string[];
  groups_claim: string;
  notes: string;
}

export interface AuthProvidersResponse {
  security_mode: string;
  active_template: string;
  templates: Record<string, AuthProviderTemplate>;
}

export interface RoleMappingPreviewResponse {
  groups: string[];
  roles: string[];
  capabilities: string[];
  note: string;
}

export async function getAuthProviders(): Promise<AuthProvidersResponse> {
  const response = await fetch(`${GATEWAY_URL}/v1/auth/providers`);
  return parseJson<AuthProvidersResponse>(response);
}

export async function previewRoleMapping(
  groups: string[],
): Promise<RoleMappingPreviewResponse> {
  const response = await fetch(`${GATEWAY_URL}/v1/auth/preview-mapping`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ groups }),
  });
  return parseJson<RoleMappingPreviewResponse>(response);
}

export async function getConvyxStatus(): Promise<{
  enabled: boolean;
  availability: string;
  detail?: string;
}> {
  const response = await fetch(`${GATEWAY_URL}/v1/convyx/status`);
  return parseJson(response);
}
