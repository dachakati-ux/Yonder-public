"use client";

import { use } from "react";
import { LessonWorkspace } from "@/features/academy/LessonWorkspace";

export default function AcademyLessonPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return <LessonWorkspace lessonId={id} />;
}
