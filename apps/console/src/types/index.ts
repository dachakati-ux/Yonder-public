export type MetricAvailability =
  | "available"
  | "not_supported"
  | "not_reported"
  | "permission_unavailable"
  | "worker_disconnected"
  | "collection_failed";

export interface MetricValue {
  availability: MetricAvailability;
  value?: string | number | boolean | null;
  unit?: string | null;
  message?: string | null;
}

export interface ModelCard {
  id: string;
  object: "model";
  created: number;
  owned_by: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  model_alias: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export type GenerationMessageMetadata = {
  kind: "generation";
  job_id: string;
  artifact_id?: string | null;
  mime_type?: string | null;
  mode: "txt2img" | "txt2video" | string;
  status: string;
  url?: string | null;
  progress_percent?: number | null;
  progress_message?: string | null;
};

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  role: "system" | "user" | "assistant";
  content: string;
  request_id?: string | null;
  metadata?: GenerationMessageMetadata | Record<string, unknown> | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  principal_subject: string;
  title: string;
  model_alias: string;
  created_at: string;
  updated_at: string;
  messages: ConversationMessage[];
}

export interface YonderErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string;
    details?: Record<string, unknown>;
  };
}

export interface ChatUiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  requestId?: string | null;
  pending?: boolean;
  error?: string | null;
  metadata?: GenerationMessageMetadata | Record<string, unknown> | null;
}

export type ChatComposerMode = "chat" | "image" | "video";

export type LabAbilityId =
  | "tools"
  | "rag"
  | "structured"
  | "guided"
  | "generation";
export type LabGuidedMode = "summarize" | "critique" | "explain" | "coach";
export type LabMediaKind = "image" | "video";
export type LabGenerationMode =
  | "txt2img"
  | "img2img"
  | "txt2video"
  | "image2video";
export type LabAspectRatio = "1:1" | "16:9" | "9:16" | "4:3" | "3:4";
export type LabStylePreset =
  | "yonder-brand"
  | "photoreal"
  | "illustration"
  | "none";

export interface LabGenerationOptions {
  media_kind?: LabMediaKind;
  mode?: LabGenerationMode;
  aspect_ratio?: LabAspectRatio;
  negative_prompt?: string;
  seed?: number | null;
  steps?: number;
  guidance?: number;
  strength?: number | null;
  style_preset?: LabStylePreset;
  reference_note?: string;
}

export interface LabAbilityCard {
  id: LabAbilityId;
  title: string;
  summary: string;
  wave: number;
}

export interface LabGenerationAsset {
  asset_id?: string;
  kind?: string;
  mime_type?: string;
  width?: number;
  height?: number;
  fps?: number;
  duration_s?: number;
  status?: string;
  source?: string;
  note?: string;
  preview_url?: string;
  frames?: Array<{
    index?: number;
    timecode_s?: number;
    preview_url?: string;
  }>;
}

export interface LabRunResult {
  ability: string;
  request_id: string;
  mode: string;
  provider: string;
  steps: Array<Record<string, unknown>>;
  answer: string;
  citations: Array<{
    id?: string;
    title?: string;
    snippet?: string;
  }>;
  structured: {
    schema_id?: string;
    data?: Record<string, unknown>;
  } | null;
  assets?: LabGenerationAsset[];
  teaching: {
    how_it_works: string[];
    how_to_build: string[];
  };
  guided_mode?: string | null;
  media_kind?: string | null;
}
