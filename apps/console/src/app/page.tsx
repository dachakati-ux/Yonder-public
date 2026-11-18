"use client";

import { Suspense } from "react";
import { ChatWorkspace } from "@/features/chat/ChatWorkspace";

export default function ChatPage() {
  return (
    <Suspense fallback={<div className="lab-layout">Loading chat…</div>}>
      <ChatWorkspace />
    </Suspense>
  );
}
