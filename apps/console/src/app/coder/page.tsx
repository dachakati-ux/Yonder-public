"use client";

import { Suspense } from "react";
import { CoderDesk } from "@/features/coder/CoderDesk";

export default function CoderPage() {
  return (
    <Suspense fallback={<div className="lab-layout">Loading coder desk…</div>}>
      <CoderDesk />
    </Suspense>
  );
}
