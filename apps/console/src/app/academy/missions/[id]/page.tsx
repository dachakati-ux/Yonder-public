"use client";

import { use } from "react";
import { MissionWorkspace } from "@/features/academy/MissionWorkspace";

export default function MissionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return <MissionWorkspace missionId={id} />;
}
