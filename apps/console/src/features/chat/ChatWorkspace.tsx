"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ConversationSidebar } from "@/components/ConversationSidebar";
import { MessageBubble } from "@/components/MessageBubble";
import { parseChatStarter } from "@/features/recipes/chatStarter";
import { saveToWorkbench } from "@/features/academy/workbenchSave";
import {
  CODER_ALIAS,
  CODER_SYSTEM_PROMPT,
  coderSessionTrustChips,
} from "@/features/coder/coderContract";
import { VoiceChatPicker } from "@/features/chat/VoiceChatPicker";
import { routeYonderCommand } from "@/features/chat/yonderCommands";
import {
  IconAutoSend,
  IconMic,
  IconSpeaker,
  IconSpeakerOff,
  IconSpinner,
  IconStop,
} from "@/features/chat/VoiceIcons";
import {
  GatewayError,
  appendMessage,
  createConversation,
  createGenerationJob,
  deleteConversation,
  getCapacitySnapshot,
  getConversation,
  getSpeechStatus,
  listConversations,
  listModels,
  renameConversation,
  replaceAssistantMessage,
  speechAudioUrl,
  streamChatCompletion,
  synthesizeSpeech,
  transcribeSpeech,
  waitForGenerationJob,
} from "@/lib/gateway";
import {
  formatContextMeterLabel,
  type ContextMeter,
} from "@/lib/contextMeter";
import type {
  ChatComposerMode,
  ChatUiMessage,
  ConversationSummary,
  GenerationMessageMetadata,
  ModelCard,
} from "@/types";

type VoiceUiState = "idle" | "recording" | "transcribing" | "speaking";

const VOICE_AUTOPLAY_KEY = "yonder.voice.autoPlay";
const VOICE_AUTOSEND_KEY = "yonder.voice.autoSend";

function toUiMessages(
  messages: Array<{
    id: string;
    role: string;
    content: string;
    request_id?: string | null;
    metadata?: ChatUiMessage["metadata"];
  }>,
): ChatUiMessage[] {
  return messages
    .filter((message) => message.role === "user" || message.role === "assistant")
    .map((message) => ({
      id: message.id,
      role: message.role as "user" | "assistant",
      content: message.content,
      requestId: message.request_id,
      metadata: message.metadata ?? null,
    }));
}

function generationAliasFor(mode: ChatComposerMode): string | null {
  if (mode === "image") return "yonder-image";
  if (mode === "video") return "yonder-video";
  return null;
}

export function ChatWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const starter = useMemo(() => parseChatStarter(searchParams), [searchParams]);
  const [models, setModels] = useState<ModelCard[]>([]);
  const [modelAlias, setModelAlias] = useState("yonder-chat");
  const [composerMode, setComposerMode] = useState<ChatComposerMode>("chat");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatUiMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [systemNote, setSystemNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [bootError, setBootError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [contextMeter, setContextMeter] = useState<ContextMeter | null>(null);
  const [modelsReady, setModelsReady] = useState(false);
  const [aliasAvailability, setAliasAvailability] = useState<string | null>(null);
  const [generationEnabled, setGenerationEnabled] = useState(false);
  const [saveNote, setSaveNote] = useState<string | null>(null);
  const [speechAvailability, setSpeechAvailability] = useState<string>("not_reported");
  const [voiceState, setVoiceState] = useState<VoiceUiState>("idle");
  const [voiceNote, setVoiceNote] = useState<string | null>(null);
  const [voiceAutoPlay, setVoiceAutoPlay] = useState(false);
  const [voiceAutoSend, setVoiceAutoSend] = useState(true);
  const [chatPickerOpen, setChatPickerOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const starterApplied = useRef(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaChunksRef = useRef<Blob[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const coderDesk = starter.desk === "coder";
  const generationAlias = generationAliasFor(composerMode);
  const activeModelLabel = useMemo(
    () => generationAlias ?? modelAlias,
    [generationAlias, modelAlias],
  );
  const generationReady = Boolean(
    generationEnabled &&
      generationAlias &&
      aliasAvailability === "ready",
  );
  const coderChips = useMemo(
    () =>
      coderDesk
        ? coderSessionTrustChips({
            aliasAvailability,
            hasWorkspace: starter.coderWorkspace,
          })
        : [],
    [coderDesk, aliasAvailability, starter.coderWorkspace],
  );

  async function refreshConversations(selectId?: string | null) {
    const items = await listConversations();
    setConversations(items);
    if (selectId !== undefined) {
      setActiveId(selectId);
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [modelCards, items] = await Promise.all([
          listModels(),
          listConversations(),
        ]);
        if (cancelled) return;
        setModels(modelCards);
        setConversations(items);
        setModelsReady(true);
        const hasStarter = Boolean(
          starter.prompt || starter.model || starter.recipeId || starter.desk,
        );
        if (coderDesk) {
          setModelAlias(CODER_ALIAS);
        } else if (modelCards.length > 0 && !starter.model) {
          setModelAlias(modelCards[0].id);
        }
        // Recipe deep-links start a fresh composer — do not auto-open last chat.
        if (items.length > 0 && !hasStarter) {
          setActiveId(items[0].id);
        }
      } catch (error) {
        if (cancelled) return;
        const message =
          error instanceof GatewayError
            ? `${error.message} (${error.code}, ${error.requestId})`
            : error instanceof Error
              ? error.message
              : "Failed to reach Yonder Gateway";
        setBootError(message);
      }
    })();
    return () => {
      cancelled = true;
    };
    // starter fields intentionally read once for boot gating
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (starterApplied.current) return;
    if (!starter.prompt && !starter.model && !starter.system && !coderDesk) return;
    if (starter.prompt) {
      setDraft(starter.prompt);
    }
    if (starter.system) {
      setSystemNote(starter.system);
    } else if (coderDesk) {
      setSystemNote(CODER_SYSTEM_PROMPT);
    }
    starterApplied.current = true;
  }, [starter, coderDesk]);

  useEffect(() => {
    if (!modelsReady) return;
    if (coderDesk) {
      setModelAlias(CODER_ALIAS);
      return;
    }
    if (!starter.model) return;
    if (models.some((m) => m.id === starter.model)) {
      setModelAlias(starter.model);
    }
  }, [starter.model, models, modelsReady, coderDesk]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const snap = await getCapacitySnapshot();
        if (cancelled) return;
        setGenerationEnabled(Boolean(snap.lab_runtime.generation_enabled));
        const trackedAlias = generationAlias ?? modelAlias;
        const row = snap.aliases.find((a) => a.alias === trackedAlias);
        if (row) {
          setAliasAvailability(
            row.ready ? "ready" : row.availability || "not_ready",
          );
          return;
        }
        setAliasAvailability(
          snap.lab_runtime.chat_alias === trackedAlias
            ? snap.lab_runtime.chat_alias_ready
              ? "ready"
              : "not_ready"
            : "not_reported",
        );
      } catch {
        if (!cancelled) {
          setAliasAvailability("not_reported");
          setGenerationEnabled(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [modelAlias, generationAlias, composerMode]);

  async function saveMessageToWorkbench(message: ChatUiMessage) {
    try {
      const prior = [...messages]
        .reverse()
        .find((m) => m.role === "user" && messages.indexOf(m) < messages.indexOf(message));
      const body = [
        prior ? `USER:\n${prior.content}` : null,
        `ASSISTANT:\n${message.content}`,
        message.requestId ? `request_id: ${message.requestId}` : null,
        starter.recipeId ? `recipe: ${starter.recipeId}` : null,
      ]
        .filter(Boolean)
        .join("\n\n");
      await saveToWorkbench({
        title: starter.recipeId
          ? `Chat evidence — ${starter.recipeId}`
          : "Chat evidence",
        body,
        tags: [
          "chat",
          ...(starter.recipeId ? [starter.recipeId] : []),
          ...(message.requestId ? [`req:${message.requestId}`] : []),
        ],
        source: "chat",
      });
      setSaveNote("Saved to Workbench");
      window.setTimeout(() => setSaveNote(null), 2500);
    } catch (err) {
      setSaveNote(err instanceof Error ? err.message : "Workbench save failed");
    }
  }

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const conversation = await getConversation(activeId);
        if (cancelled) return;
        setModelAlias(conversation.model_alias);
        setMessages(toUiMessages(conversation.messages));
      } catch (error) {
        if (cancelled) return;
        setBootError(
          error instanceof Error ? error.message : "Failed to load conversation",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  useEffect(() => {
    threadRef.current?.scrollTo({
      top: threadRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, busy]);

  useEffect(() => {
    try {
      setVoiceAutoPlay(window.localStorage.getItem(VOICE_AUTOPLAY_KEY) === "1");
      const autoSend = window.localStorage.getItem(VOICE_AUTOSEND_KEY);
      setVoiceAutoSend(autoSend === null ? true : autoSend === "1");
    } catch {
      setVoiceAutoPlay(false);
      setVoiceAutoSend(true);
    }
    let cancelled = false;
    (async () => {
      try {
        const status = await getSpeechStatus();
        if (!cancelled) {
          setSpeechAvailability(status.availability || "not_reported");
        }
      } catch {
        if (!cancelled) setSpeechAvailability("not_reported");
      }
    })();
    return () => {
      cancelled = true;
      mediaRecorderRef.current?.stop();
      audioRef.current?.pause();
    };
  }, []);

  const speechReady =
    speechAvailability === "ready" || speechAvailability === "mock";

  function setAutoPlay(next: boolean) {
    setVoiceAutoPlay(next);
    try {
      window.localStorage.setItem(VOICE_AUTOPLAY_KEY, next ? "1" : "0");
    } catch {
      /* ignore */
    }
  }

  function setAutoSend(next: boolean) {
    setVoiceAutoSend(next);
    try {
      window.localStorage.setItem(VOICE_AUTOSEND_KEY, next ? "1" : "0");
    } catch {
      /* ignore */
    }
  }

  async function playAssistantSpeech(text: string) {
    if (!text.trim() || !speechReady || !voiceAutoPlay) return;
    setVoiceState("speaking");
    try {
      const result = await synthesizeSpeech(text);
      if (!result.url) {
        setVoiceNote(`TTS ${result.availability}`);
        return;
      }
      const audio = new Audio(speechAudioUrl(result.url));
      audioRef.current = audio;
      await audio.play();
      await new Promise<void>((resolve) => {
        audio.onended = () => resolve();
        audio.onerror = () => resolve();
      });
    } catch (error) {
      setVoiceNote(
        error instanceof GatewayError
          ? `${error.message} (${String(error.details?.availability ?? error.code)})`
          : error instanceof Error
            ? error.message
            : "TTS failed",
      );
    } finally {
      setVoiceState("idle");
    }
  }

  async function applyTranscript(text: string): Promise<"sent" | "draft" | "command"> {
    const routed = routeYonderCommand(text);
    if (routed.kind === "none") {
      setDraft(routed.draft);
      if (routed.draft && voiceAutoSend) {
        await handleSendText(routed.draft);
        return "sent";
      }
      return "draft";
    }
    if (routed.kind === "unknown") {
      setVoiceNote(routed.message);
      setDraft(routed.draft);
      return "draft";
    }
    if (routed.kind === "navigate") {
      setVoiceNote(`Opening ${routed.target}`);
      if (routed.remainder) setDraft(routed.remainder);
      router.push(routed.href);
      return "command";
    }
    // open_chat — ask new vs existing; never silent create
    setChatPickerOpen(true);
    if (routed.remainder) setDraft(routed.remainder);
    return "command";
  }

  async function toggleMic() {
    if (voiceState === "recording") {
      mediaRecorderRef.current?.stop();
      return;
    }
    if (!speechReady) {
      setVoiceNote(`Speech ${speechAvailability} — mic disabled until Gateway is ready`);
      return;
    }
    if (composerMode !== "chat") {
      setVoiceNote("Voice dictation is available in Chat mode");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      mediaChunksRef.current = [];
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) mediaChunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        void (async () => {
          setVoiceState("transcribing");
          try {
            const blob = new Blob(mediaChunksRef.current, {
              type: recorder.mimeType || "audio/webm",
            });
            const result = await transcribeSpeech(blob);
            setSpeechAvailability(result.availability || speechAvailability);
            await applyTranscript(result.text || "");
          } catch (error) {
            setVoiceNote(
              error instanceof GatewayError
                ? `${error.message} (${String(error.details?.availability ?? error.code)})`
                : error instanceof Error
                  ? error.message
                  : "Transcribe failed",
            );
          } finally {
            setVoiceState("idle");
          }
        })();
      };
      recorder.start();
      setVoiceState("recording");
      setVoiceNote(null);
    } catch (error) {
      setVoiceNote(
        error instanceof Error ? error.message : "Microphone permission denied",
      );
      setVoiceState("idle");
    }
  }

  async function handleCreate() {
    const conversation = await createConversation({
      title: "New conversation",
      model_alias: modelAlias,
    });
    await refreshConversations(conversation.id);
    setMessages([]);
  }

  async function handleRename(id: string) {
    const current = conversations.find((item) => item.id === id);
    const next = window.prompt("Rename conversation", current?.title ?? "");
    if (!next || !next.trim()) return;
    await renameConversation(id, next.trim());
    await refreshConversations(id);
  }

  async function handleDelete(id: string) {
    if (!window.confirm("Delete this conversation?")) return;
    await deleteConversation(id);
    const remaining = conversations.filter((item) => item.id !== id);
    setConversations(remaining);
    if (activeId === id) {
      setActiveId(remaining[0]?.id ?? null);
      setMessages([]);
    }
  }

  function stopGeneration() {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  }

  async function runCompletion(options: {
    conversationId: string;
    history: ChatUiMessage[];
    regenerate?: boolean;
  }) {
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setBootError(null);

    const assistantId = `local_asst_${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: assistantId,
        role: "assistant",
        content: "",
        pending: true,
      },
    ]);

    let requestIdLocal: string | null = null;
    try {
      const apiMessages: Array<{ role: "system" | "user" | "assistant"; content: string }> =
        [];
      if (systemNote) {
        apiMessages.push({ role: "system", content: systemNote });
      }
      for (const message of options.history) {
        apiMessages.push({ role: message.role, content: message.content });
      }
      const content = await streamChatCompletion({
        model: modelAlias,
        messages: apiMessages,
        conversationId: options.conversationId,
        signal: controller.signal,
        onRequestId: (id) => {
          requestIdLocal = id;
          setRequestId(id);
        },
        onContext: (event) => {
          setContextMeter(event.meter);
          if (event.condensed && event.messages.length > 0) {
            const synced = toUiMessages(event.messages);
            setMessages((prev) => {
              const pending = prev.find((message) => message.pending);
              return pending ? [...synced, pending] : synced;
            });
          }
        },
        onToken: (token) => {
          setMessages((prev) =>
            prev.map((message) =>
              message.id === assistantId
                ? { ...message, content: message.content + token }
                : message,
            ),
          );
        },
      });

      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantId
            ? { ...message, content, pending: false, requestId: requestIdLocal }
            : message,
        ),
      );

      if (options.regenerate) {
        await replaceAssistantMessage(options.conversationId, {
          content,
          request_id: requestIdLocal,
        });
      } else {
        await appendMessage(options.conversationId, {
          role: "assistant",
          content,
          request_id: requestIdLocal,
        });
      }
      await refreshConversations(options.conversationId);
      void playAssistantSpeech(content);
    } catch (error) {
      if (controller.signal.aborted) {
        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  pending: false,
                  error: "Generation stopped.",
                }
              : message,
          ),
        );
        return;
      }
      const message =
        error instanceof GatewayError
          ? `${error.message} [${error.code}] request_id=${error.requestId}`
          : error instanceof Error
            ? error.message
            : "Chat failed";
      setMessages((prev) =>
        prev.map((item) =>
          item.id === assistantId
            ? { ...item, pending: false, error: message }
            : item,
        ),
      );
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  async function runGeneration(options: {
    conversationId: string;
    prompt: string;
    mode: "image" | "video";
  }) {
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setBootError(null);

    const assistantId = `local_asst_${Date.now()}`;
    const jobMode = options.mode === "video" ? "txt2video" : "txt2img";
    const alias = generationAliasFor(options.mode) ?? "yonder-image";
    const pendingMeta: GenerationMessageMetadata = {
      kind: "generation",
      job_id: "",
      mode: jobMode,
      status: "queued",
    };
    setMessages((prev) => [
      ...prev,
      {
        id: assistantId,
        role: "assistant",
        content: options.mode === "video" ? "Generating video…" : "Generating image…",
        pending: true,
        metadata: pendingMeta,
      },
    ]);

    try {
      if (!generationReady) {
        throw new Error(
          `${alias} is not ready. Load a Diffusers model on Models, then try again.`,
        );
      }
      const created = await createGenerationJob({
        mode: jobMode,
        prompt: options.prompt,
        aspect_ratio: options.mode === "video" ? "16:9" : "1:1",
        steps: options.mode === "video" ? 20 : 4,
        guidance: 0,
        model: alias,
      });
      setRequestId(created.request_id);
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: `Generating ${options.mode}…`,
                requestId: created.request_id,
                metadata: {
                  kind: "generation",
                  job_id: created.id,
                  mode: jobMode,
                  status: created.status,
                },
              }
            : message,
        ),
      );

      const finished = await waitForGenerationJob(created.id, {
        signal: controller.signal,
        maxAttempts: 400,
        intervalMs: 1500,
        onUpdate: (live) => {
          const latest = live.progress?.[live.progress.length - 1];
          setMessages((prev) =>
            prev.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content: latest?.message
                      ? latest.message
                      : `Generating ${options.mode}…`,
                    metadata: {
                      kind: "generation",
                      job_id: live.id,
                      mode: jobMode,
                      status: live.status,
                      progress_percent:
                        typeof latest?.percent === "number" ? latest.percent : null,
                      progress_message: latest?.message ?? null,
                    },
                  }
                : message,
            ),
          );
        },
      });
      const asset = finished.assets?.[0];
      const metadata: GenerationMessageMetadata = {
        kind: "generation",
        job_id: finished.id,
        artifact_id: asset?.artifact_id ?? null,
        mime_type: asset?.mime_type ?? null,
        mode: jobMode,
        status: finished.status,
        url: asset?.url ?? null,
      };
      const caption =
        finished.status === "succeeded" || finished.status === "completed"
          ? options.mode === "video"
            ? "Here’s the video."
            : "Here’s the image."
          : `Generation ${finished.status}${finished.error ? `: ${finished.error}` : ""}`;

      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: caption,
                pending: false,
                requestId: finished.request_id,
                metadata,
                error: finished.error ?? null,
              }
            : message,
        ),
      );
      await appendMessage(options.conversationId, {
        role: "assistant",
        content: caption,
        request_id: finished.request_id,
        metadata,
      });
      await refreshConversations(options.conversationId);
    } catch (error) {
      if (controller.signal.aborted) {
        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  pending: false,
                  content: "Generation stopped.",
                  error: "Generation stopped.",
                  metadata: {
                    kind: "generation",
                    job_id: (message.metadata as GenerationMessageMetadata | undefined)
                      ?.job_id ?? "",
                    mode: jobMode,
                    status: "cancelled",
                  },
                }
              : message,
          ),
        );
        return;
      }
      const messageText =
        error instanceof GatewayError
          ? `${error.message} [${error.code}] request_id=${error.requestId}`
          : error instanceof Error
            ? error.message
            : "Generation failed";
      setMessages((prev) =>
        prev.map((item) =>
          item.id === assistantId
            ? {
                ...item,
                pending: false,
                content: messageText,
                error: messageText,
                metadata: {
                  kind: "generation",
                  job_id:
                    (item.metadata as GenerationMessageMetadata | undefined)?.job_id ??
                    "",
                  mode: jobMode,
                  status: "failed",
                },
              }
            : item,
        ),
      );
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  async function handleSend() {
    await handleSendText(draft);
  }

  async function handleSendText(raw: string) {
    const content = raw.trim();
    if (!content || busy) return;

    let conversationId = activeId;
    if (!conversationId) {
      const title = content.slice(0, 48);
      const conversation = await createConversation({
        title,
        model_alias: generationAlias ?? modelAlias,
      });
      conversationId = conversation.id;
      await refreshConversations(conversationId);
    }

    const userMessage: ChatUiMessage = {
      id: `local_user_${Date.now()}`,
      role: "user",
      content,
    };
    const history = [...messages, userMessage];
    setMessages(history);
    setDraft("");
    await appendMessage(conversationId, { role: "user", content });
    if (composerMode === "image" || composerMode === "video") {
      await runGeneration({
        conversationId,
        prompt: content,
        mode: composerMode,
      });
      return;
    }
    await runCompletion({ conversationId, history });
  }

  async function handleRegenerate() {
    if (!activeId || busy) return;
    const lastUserIndex = [...messages]
      .map((message, index) => ({ message, index }))
      .reverse()
      .find((entry) => entry.message.role === "user");
    if (!lastUserIndex) return;

    const history = messages.slice(0, lastUserIndex.index + 1);
    setMessages(history);
    await runCompletion({
      conversationId: activeId,
      history,
      regenerate: true,
    });
  }

  return (
    <div className="chat-layout">
      <ConversationSidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={setActiveId}
        onCreate={() => void handleCreate()}
        onRename={(id) => void handleRename(id)}
        onDelete={(id) => void handleDelete(id)}
      />
      <section className="chat-main">
        <div className="chat-toolbar">
          <div className="composer-mode" role="group" aria-label="Composer mode">
            {(
              [
                ["chat", "Chat"],
                ["image", "Image"],
                ["video", "Video"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                data-active={composerMode === id ? "true" : "false"}
                disabled={busy}
                onClick={() => setComposerMode(id)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="model-chip">
            {composerMode === "chat" ? "Active model" : "Generation alias"}
            <strong>{activeModelLabel}</strong>
            {composerMode === "chat" ? (
              <select
                className="status-select"
                value={modelAlias}
                disabled={busy || models.length === 0 || coderDesk}
                onChange={(event) => setModelAlias(event.target.value)}
                aria-label="Model alias"
              >
                {models.length === 0 ||
                (coderDesk && !models.some((m) => m.id === CODER_ALIAS)) ? (
                  <option value={modelAlias}>{modelAlias}</option>
                ) : (
                  (coderDesk
                    ? models.filter((m) => m.id === CODER_ALIAS)
                    : models
                  ).map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.id}
                    </option>
                  ))
                )}
              </select>
            ) : null}
          </div>
          <div className="model-chip">
            {requestId
              ? `last request: ${requestId}`
              : composerMode === "chat"
                ? "gateway: /v1/chat/completions"
                : "gateway: /v1/generation/jobs"}
          </div>
          {composerMode === "chat" ? (
            <div className="model-chip" title="Context pressure from Gateway">
              {formatContextMeterLabel(contextMeter)}
            </div>
          ) : null}
          <div
            className="model-chip"
            title="Alias readiness from Capacity snapshot — never invent READY"
          >
            alias: {aliasAvailability ?? "not_reported"}
          </div>
          {composerMode !== "chat" && !generationReady ? (
            <div className="model-chip">
              gen unavailable ·{" "}
              <Link href="/models">Load on Models</Link>
            </div>
          ) : null}
          {starter.recipeId ? (
            <div className="model-chip">recipe: {starter.recipeId}</div>
          ) : null}
          {coderDesk ? (
            <div className="academy-criteria-chips" aria-label="Coder trust chips">
              {coderChips.map((chip) => (
                <span
                  key={chip.id}
                  className={
                    chip.kind === "ok"
                      ? "academy-chip academy-chip-met"
                      : chip.kind === "warn"
                        ? "academy-chip academy-chip-missed"
                        : "academy-chip"
                  }
                >
                  {chip.label}
                </span>
              ))}
            </div>
          ) : null}
          {saveNote ? <div className="model-chip">{saveNote}</div> : null}
          <div
            className="model-chip"
            title="Speech path readiness from Gateway — never invent listening"
          >
            speech: {speechAvailability}
            {voiceState !== "idle" ? ` · ${voiceState}` : ""}
          </div>
          {voiceNote ? <div className="model-chip">{voiceNote}</div> : null}
        </div>

        {systemNote ? (
          <div className="lab-banner">
            {coderDesk
              ? "Coder system contract active (read-only — sent with the next completion). "
              : "Recipe system prompt active (sent with the next completion). "}
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setSystemNote(null)}
            >
              Clear
            </button>
            {coderDesk ? (
              <>
                {" "}
                <Link href="/coder">Coder desk</Link>
              </>
            ) : null}
          </div>
        ) : null}

        <div className="chat-thread" ref={threadRef}>
          {bootError ? <div className="message-error">{bootError}</div> : null}
          {messages.length === 0 && !bootError ? (
            <div className="empty-state empty-state-hero">
              <p className="empty-kicker">
                {coderDesk ? "Coder · read-only" : "Intelligence beyond search"}
              </p>
              <h3>
                {coderDesk ? "Paste code. Propose patches." : "Ask Yonder"}
              </h3>
              <p>
                {coderDesk
                  ? aliasAvailability === "ready"
                    ? "yonder-coder is ready. Diffs are proposals only — no workspace writes from Chat."
                    : `yonder-coder is ${aliasAvailability ?? "not_reported"}. Load the two-GPU coder lane before a live VIP session.`
                  : "Chat streams through the gateway. Switch to Image or Video to run Diffusers jobs when a generation model is loaded — readiness comes from Capacity, never invent READY."}
              </p>
              <div className="academy-hero-actions">
                {coderDesk ? (
                  <>
                    <Link className="btn" href="/coder">
                      Coder desk
                    </Link>
                    <Link className="btn btn-ghost" href="/academy/capacity">
                      Capacity
                    </Link>
                    <Link className="btn btn-ghost" href="/models">
                      Models
                    </Link>
                  </>
                ) : (
                  <>
                    <Link className="btn" href="/recipes">
                      Recipes catalog
                    </Link>
                    <Link className="btn btn-ghost" href="/models">
                      Models
                    </Link>
                    <Link className="btn btn-ghost" href="/studio">
                      Studio
                    </Link>
                    <Link className="btn btn-ghost" href="/academy/capacity">
                      Capacity
                    </Link>
                  </>
                )}
              </div>
            </div>
          ) : null}
          {messages.map((message, index) => (
            <MessageBubble
              key={message.id}
              message={message}
              canRegenerate={
                !busy &&
                message.role === "assistant" &&
                index === messages.length - 1 &&
                (message.metadata as GenerationMessageMetadata | undefined)?.kind !==
                  "generation"
              }
              onCopy={() => void navigator.clipboard.writeText(message.content)}
              onRegenerate={() => void handleRegenerate()}
              onSaveWorkbench={
                message.role === "assistant" && !message.pending
                  ? () => void saveMessageToWorkbench(message)
                  : undefined
              }
            />
          ))}
        </div>

        <div className="composer">
          {composerMode !== "chat" && !generationReady ? (
            <p className="entity-meta" style={{ maxWidth: "52rem", margin: "0 auto 0.65rem" }}>
              {generationAlias ?? "Generation"} is not ready.{" "}
              <Link href="/models">Load an image/video model on Models</Link>, keep
              it off GPUs running llama.cpp, then return here.
            </p>
          ) : null}
          <div className="composer-row composer-row-voice">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={
                composerMode === "image"
                  ? "Describe an image…"
                  : composerMode === "video"
                    ? "Describe a short video…"
                    : "Message Yonder… (say “Yonder …” for commands)"
              }
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void handleSend();
                }
              }}
              disabled={Boolean(bootError)}
            />
            <div className="composer-voice-actions">
              <button
                type="button"
                className="btn btn-icon"
                data-active={voiceState === "recording" ? "true" : "false"}
                onClick={() => void toggleMic()}
                disabled={
                  Boolean(bootError) ||
                  composerMode !== "chat" ||
                  voiceState === "transcribing" ||
                  voiceState === "speaking" ||
                  (!speechReady && voiceState !== "recording")
                }
                aria-label={
                  voiceState === "recording"
                    ? "Stop recording"
                    : voiceState === "transcribing"
                      ? "Transcribing"
                      : "Record voice"
                }
                title={
                  speechReady
                    ? voiceState === "recording"
                      ? "Stop recording"
                      : "Record → Gateway STT"
                    : `Speech ${speechAvailability}`
                }
              >
                {voiceState === "recording" ? (
                  <IconStop />
                ) : voiceState === "transcribing" ? (
                  <IconSpinner className="icon-spin" />
                ) : (
                  <IconMic />
                )}
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                data-active={voiceAutoPlay ? "true" : "false"}
                onClick={() => setAutoPlay(!voiceAutoPlay)}
                disabled={!speechReady}
                aria-label={
                  voiceAutoPlay
                    ? "Speaker on — click to mute replies"
                    : "Speaker off — click to auto-play replies"
                }
                title={
                  speechReady
                    ? voiceAutoPlay
                      ? "Auto-play replies on"
                      : "Auto-play replies off"
                    : `Speech ${speechAvailability}`
                }
              >
                {voiceAutoPlay ? <IconSpeaker /> : <IconSpeakerOff />}
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                data-active={voiceAutoSend ? "true" : "false"}
                onClick={() => setAutoSend(!voiceAutoSend)}
                disabled={!speechReady}
                aria-label={
                  voiceAutoSend
                    ? "Auto-send dictation on — click to require Send"
                    : "Auto-send dictation off — click to send after each recording"
                }
                title={
                  voiceAutoSend
                    ? "Auto-send: recording → transcript → Send"
                    : "Manual send: recording fills the composer only"
                }
              >
                <IconAutoSend />
              </button>
              {busy ? (
                <button type="button" className="btn btn-danger" onClick={stopGeneration}>
                  Stop
                </button>
              ) : (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => void handleSend()}
                  disabled={
                    !draft.trim() ||
                    Boolean(bootError) ||
                    (composerMode !== "chat" && !generationReady)
                  }
                >
                  {composerMode === "chat" ? "Send" : "Generate"}
                </button>
              )}
            </div>
          </div>
        </div>
      </section>
      <VoiceChatPicker
        open={chatPickerOpen}
        conversations={conversations}
        activeId={activeId}
        onUseCurrent={() => {
          setChatPickerOpen(false);
          setVoiceNote("Using current conversation");
        }}
        onPickExisting={(id) => {
          setActiveId(id);
          setChatPickerOpen(false);
          setVoiceNote("Switched conversation");
        }}
        onStartNew={() => {
          setChatPickerOpen(false);
          void handleCreate();
        }}
        onDismiss={() => setChatPickerOpen(false)}
      />
    </div>
  );
}
