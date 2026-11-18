/** Parse Chat deep-link / recipe starter query params. */

export interface ChatStarter {
  recipeId: string | null;
  prompt: string | null;
  model: string | null;
  system: string | null;
  /** When `coder`, Chat locks to the Phase A coder assistant contract. */
  desk: string | null;
  coderWorkspace: boolean;
}

export function parseChatStarter(
  params: URLSearchParams | { get(name: string): string | null },
): ChatStarter {
  const prompt = params.get("prompt");
  const model = params.get("model");
  const system = params.get("system");
  const recipeId = params.get("recipe");
  const desk = params.get("desk");
  const coderWorkspace = params.get("coder_workspace");
  return {
    recipeId: recipeId?.trim() || null,
    prompt: prompt?.trim() || null,
    model: model?.trim() || null,
    system: system?.trim() || null,
    desk: desk?.trim() || null,
    coderWorkspace:
      coderWorkspace === "1" ||
      coderWorkspace === "true" ||
      coderWorkspace === "yes",
  };
}
