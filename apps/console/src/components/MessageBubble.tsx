"use client";

import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { ChatUiMessage, GenerationMessageMetadata } from "@/types";
import { trustChipsFromText } from "@/features/chat/trustChips";
import { GATEWAY_URL } from "@/lib/brand";
import { LoadProgressBar } from "@/components/LoadProgressBar";

function asGenerationMeta(
  metadata: ChatUiMessage["metadata"],
): GenerationMessageMetadata | null {
  if (!metadata || typeof metadata !== "object") return null;
  if ((metadata as GenerationMessageMetadata).kind !== "generation") return null;
  return metadata as GenerationMessageMetadata;
}

export function MessageBubble({
  message,
  onCopy,
  onRegenerate,
  onSaveWorkbench,
  canRegenerate,
}: {
  message: ChatUiMessage;
  onCopy?: () => void;
  onRegenerate?: () => void;
  onSaveWorkbench?: () => void;
  canRegenerate?: boolean;
}) {
  const generation = asGenerationMeta(message.metadata);
  const chips =
    message.role === "assistant" && !message.pending && !generation
      ? trustChipsFromText(message.content)
      : [];
  const artifactUrl =
    generation?.url ||
    (generation?.artifact_id
      ? `/v1/generation/artifacts/${generation.artifact_id}`
      : null);
  const absoluteArtifact = artifactUrl
    ? artifactUrl.startsWith("http")
      ? artifactUrl
      : `${GATEWAY_URL}${artifactUrl}`
    : null;
  const isVideo =
    Boolean(generation?.mime_type?.startsWith("video/")) ||
    generation?.mode === "txt2video" ||
    generation?.mode === "image2video";

  return (
    <article
      className="message"
      data-role={message.role}
      data-pending={message.pending ? "true" : "false"}
      data-generation={generation ? "true" : undefined}
    >
      <div className="message-role">
        {message.role === "assistant" ? "Yonder" : "You"}
      </div>
      <div className="message-content">
        {message.role === "assistant" ? (
          message.pending && !message.content && !generation ? (
            <span className="typing-dots" aria-label="Generating">
              <span />
              <span />
              <span />
            </span>
          ) : (
            <>
              {message.content ? (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeHighlight]}
                >
                  {message.content}
                </ReactMarkdown>
              ) : null}
              {generation ? (
                <div className="message-generation">
                  {message.pending ||
                  ["queued", "running", "accepted"].includes(generation.status) ? (
                    <LoadProgressBar
                      percent={
                        typeof generation.progress_percent === "number"
                          ? generation.progress_percent
                          : null
                      }
                      label={`Generating ${isVideo ? "video" : "image"}`}
                      detail={
                        generation.progress_message?.trim() ||
                        (generation.job_id
                          ? `Job ${generation.job_id}`
                          : "Waiting for worker progress…")
                      }
                    />
                  ) : null}
                  {absoluteArtifact &&
                  ["succeeded", "completed"].includes(generation.status) ? (
                    isVideo ? (
                      <video
                        className="message-media"
                        src={absoluteArtifact}
                        controls
                        playsInline
                      />
                    ) : (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        className="message-media"
                        src={absoluteArtifact}
                        alt={message.content || "Generated image"}
                      />
                    )
                  ) : null}
                  {["failed", "error", "cancelled", "rejected"].includes(
                    generation.status,
                  ) ? (
                    <p className="message-error">
                      Generation {generation.status}
                      {message.error ? `: ${message.error}` : ""}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </>
          )
        ) : (
          message.content
        )}
      </div>
      {chips.length ? (
        <div className="academy-criteria-chips" aria-label="Trust signals">
          {chips.map((chip) => (
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
      {message.error && !generation ? (
        <div className="message-error">{message.error}</div>
      ) : null}
      {message.role === "assistant" && !message.pending ? (
        <div className="message-actions">
          {onCopy ? (
            <button type="button" className="btn btn-ghost" onClick={onCopy}>
              Copy
            </button>
          ) : null}
          {absoluteArtifact &&
          ["succeeded", "completed"].includes(generation?.status ?? "") ? (
            <a className="btn btn-ghost" href={absoluteArtifact} download>
              Download
            </a>
          ) : null}
          {generation?.job_id ? (
            <Link
              className="btn btn-ghost"
              href={`/studio?job=${encodeURIComponent(generation.job_id)}`}
            >
              Open in Studio
            </Link>
          ) : null}
          {onSaveWorkbench ? (
            <button type="button" className="btn btn-ghost" onClick={onSaveWorkbench}>
              Save to Workbench
            </button>
          ) : null}
          {canRegenerate ? (
            <button type="button" className="btn btn-ghost" onClick={onRegenerate}>
              Regenerate
            </button>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
