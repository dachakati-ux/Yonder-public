/** Save evidence snippets to the Academy Workbench via Gateway. */

import { createWorkbenchNote, type WorkbenchNote } from "@/lib/gateway";

export async function saveToWorkbench(input: {
  title: string;
  body: string;
  tags?: string[];
  source: "chat" | "recipe" | "manual" | "mission" | "evidence_pack";
}): Promise<WorkbenchNote> {
  const result = await createWorkbenchNote({
    title: input.title.trim() || "Untitled note",
    body: input.body,
    tags: input.tags ?? [],
    source: input.source,
  });
  return result.note;
}
