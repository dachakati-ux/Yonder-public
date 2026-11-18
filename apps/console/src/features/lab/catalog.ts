import type { LabAbilityId, LabGuidedMode, LabMediaKind } from "@/types";

export const LAB_ABILITY_META: Record<
  LabAbilityId,
  {
    title: string;
    promise: string;
    defaultPrompt: string;
    href: string;
  }
> = {
  tools: {
    title: "Tool use",
    promise: "See the plan → tool_call → tool_result → answer loop.",
    defaultPrompt: "What is the weather in Seattle?",
    href: "/lab/tools",
  },
  rag: {
    title: "RAG",
    promise: "Retrieve trusted chunks, augment the prompt, and cite sources.",
    defaultPrompt: "Does Yonder authenticate corporate users?",
    href: "/lab/rag",
  },
  structured: {
    title: "Structured output",
    promise: "Emit schema-valid JSON and validate fail-closed.",
    defaultPrompt: "Grade this good submission with a clear next step.",
    href: "/lab/structured",
  },
  guided: {
    title: "Guided modes",
    promise: "Summarize, critique, explain, or coach via system prompts.",
    defaultPrompt:
      "Yonder routes all inference through the Gateway and never invents GPU metrics.",
    href: "/lab/guided",
  },
  generation: {
    title: "AI generation",
    promise: "Image and video jobs: prompt → queue → sample → asset via Gateway.",
    defaultPrompt: "Chrome Y emblem over a dark hexagonal grid, electric cyan rim light",
    href: "/lab/generation",
  },
};

export const GUIDED_MODES: Array<{ id: LabGuidedMode; label: string }> = [
  { id: "summarize", label: "Summarize" },
  { id: "critique", label: "Critique" },
  { id: "explain", label: "Explain" },
  { id: "coach", label: "Coach" },
];

export const MEDIA_KINDS: Array<{ id: LabMediaKind; label: string }> = [
  { id: "image", label: "Image" },
  { id: "video", label: "Video" },
];

export const BUILD_PATHS: Record<LabAbilityId, string[]> = {
  tools: [
    "apps/gateway/app/api/lab.py",
    "apps/gateway/app/services/lab_scenarios.py",
    "packages/contracts/yonder_contracts/inference.py",
  ],
  rag: [
    "config/lab/corpus.yaml",
    "apps/gateway/app/services/lab_scenarios.py",
    "apps/gateway/app/api/lab.py",
  ],
  structured: [
    "apps/gateway/app/services/lab_scenarios.py",
    "apps/console/src/features/lab/AbilityWorkspace.tsx",
  ],
  guided: [
    "apps/gateway/app/services/lab_scenarios.py",
    "apps/console/src/features/chat/ChatWorkspace.tsx",
  ],
  generation: [
    "apps/gateway/app/services/lab_scenarios.py",
    "apps/gateway/app/api/lab.py",
    "docs/lab-training.md",
  ],
};
