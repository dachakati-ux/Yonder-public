"use client";

import type { ConversationSummary } from "@/types";

type Props = {
  open: boolean;
  conversations: ConversationSummary[];
  activeId: string | null;
  onUseCurrent: () => void;
  onPickExisting: (id: string) => void;
  onStartNew: () => void;
  onDismiss: () => void;
};

/** Ask new vs existing — never silently create or overwrite. */
export function VoiceChatPicker({
  open,
  conversations,
  activeId,
  onUseCurrent,
  onPickExisting,
  onStartNew,
  onDismiss,
}: Props) {
  if (!open) return null;
  const hasCurrent = Boolean(activeId);

  return (
    <div className="voice-picker-backdrop" role="presentation" onClick={onDismiss}>
      <div
        className="voice-picker"
        role="dialog"
        aria-modal="true"
        aria-labelledby="voice-chat-picker-title"
        onClick={(event) => event.stopPropagation()}
      >
        <p className="empty-kicker">Voice command</p>
        <h3 id="voice-chat-picker-title">Open chat</h3>
        <p className="muted">
          Choose a conversation. Yonder will not create or overwrite one without
          this choice.
        </p>
        <div className="voice-picker-actions">
          {hasCurrent ? (
            <button type="button" className="btn btn-primary" onClick={onUseCurrent}>
              Use current conversation
            </button>
          ) : null}
          <button type="button" className="btn" onClick={onStartNew}>
            Start new
          </button>
          <button type="button" className="btn btn-ghost" onClick={onDismiss}>
            Cancel
          </button>
        </div>
        {conversations.length > 0 ? (
          <div className="voice-picker-list">
            <p className="entity-meta">Pick existing</p>
            <ul>
              {conversations.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    data-active={item.id === activeId ? "true" : "false"}
                    onClick={() => onPickExisting(item.id)}
                  >
                    {item.title || "Untitled"}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="entity-meta">No existing conversations yet.</p>
        )}
      </div>
    </div>
  );
}
