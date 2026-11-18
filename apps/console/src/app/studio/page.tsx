"use client";

import { Suspense } from "react";
import { StudioWorkspace } from "@/features/studio/StudioWorkspace";

export default function StudioPage() {
  return (
    <Suspense fallback={<div className="lab-layout">Loading studio…</div>}>
      <StudioWorkspace />
    </Suspense>
  );
}
