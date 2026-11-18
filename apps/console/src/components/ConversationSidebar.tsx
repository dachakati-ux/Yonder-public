"use client";

import type { ConversationSummary } from "@/types";

export function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <aside className="chat-sidebar">
      <div className="sidebar-header">
        <div>
          <h2>Conversations</h2>
          <p className="entity-meta" style={{ margin: "0.2rem 0 0" }}>
            Gateway-mediated only
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={onCreate}>
          New
        </button>
      </div>
      <div className="conversation-list">
        {conversations.length === 0 ? (
          <div className="empty-state">
            <h3>Begin beyond search</h3>
            <p>Start a conversation. Every turn routes through Yonder Gateway.</p>
          </div>
        ) : (
          conversations.map((conversation) => (
            <div key={conversation.id}>
              <button
                type="button"
                className="conversation-item"
                data-active={conversation.id === activeId ? "true" : "false"}
                onClick={() => onSelect(conversation.id)}
              >
                <span className="title">{conversation.title}</span>
                <span className="meta">
                  {conversation.model_alias} · {conversation.message_count} messages
                </span>
              </button>
              {conversation.id === activeId ? (
                <div className="conversation-actions">
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => onRename(conversation.id)}
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    className="btn btn-danger"
                    onClick={() => onDelete(conversation.id)}
                  >
                    Delete
                  </button>
                </div>
              ) : null}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
