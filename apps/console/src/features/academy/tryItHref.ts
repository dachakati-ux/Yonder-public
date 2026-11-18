/** Build Academy Try it / mission step hrefs with preset, recipe, and return params. */

export type TryItLike = {
  kind?: string | null;
  href?: string | null;
  ability?: string | null;
  recipe_id?: string | null;
  chat_prompt?: string | null;
  sandbox_preset?: string | null;
};

export type TryItHrefOptions = {
  fromLesson?: string | null;
  fromMission?: string | null;
  /** Full Chat deep-link when recipe is known in the console catalog. */
  recipeChatHref?: string | null;
};

function appendReturnParams(
  href: string,
  opts?: TryItHrefOptions,
): string {
  const hashIndex = href.indexOf("#");
  const hash = hashIndex >= 0 ? href.slice(hashIndex) : "";
  const withoutHash = hashIndex >= 0 ? href.slice(0, hashIndex) : href;
  const qIndex = withoutHash.indexOf("?");
  const path = qIndex >= 0 ? withoutHash.slice(0, qIndex) : withoutHash;
  const params = new URLSearchParams(
    qIndex >= 0 ? withoutHash.slice(qIndex + 1) : "",
  );
  if (opts?.fromLesson) params.set("fromLesson", opts.fromLesson);
  if (opts?.fromMission) params.set("fromMission", opts.fromMission);
  const qs = params.toString();
  return `${path}${qs ? `?${qs}` : ""}${hash}`;
}

function sandboxHref(preset: string): string {
  return `/academy/sandbox?preset=${encodeURIComponent(preset)}`;
}

function recipeHref(recipeId: string, recipeChatHref?: string | null): string {
  if (recipeChatHref) return recipeChatHref;
  return `/?recipe=${encodeURIComponent(recipeId)}`;
}

/**
 * Prefer structured fields (sandbox_preset, recipe_id, ability) over a bare href
 * so YAML that lists `/academy/sandbox` without `?preset=` still lands correctly.
 */
export function buildTryItHref(
  tryIt: TryItLike | null | undefined,
  opts?: TryItHrefOptions,
): string {
  if (!tryIt) return appendReturnParams("/lab", opts);

  const preset = tryIt.sandbox_preset?.trim() || null;
  const recipeId = tryIt.recipe_id?.trim() || null;
  const ability = tryIt.ability?.trim() || null;
  const raw = (tryIt.href || "").trim();

  let base: string;
  if (preset && (!raw || raw === "/academy/sandbox" || raw.startsWith("/academy/sandbox"))) {
    base = sandboxHref(preset);
  } else if (recipeId && (!raw || raw === "/recipes" || raw.startsWith("/recipes"))) {
    base = recipeHref(recipeId, opts?.recipeChatHref);
  } else if (ability && (!raw || raw === "/lab" || raw.startsWith("/lab/"))) {
    base = `/lab/${ability}`;
  } else if (raw) {
    base = raw;
    if (preset && base.startsWith("/academy/sandbox") && !base.includes("preset=")) {
      base = sandboxHref(preset);
    }
    if (recipeId && (base === "/recipes" || base.startsWith("/recipes?"))) {
      base = recipeHref(recipeId, opts?.recipeChatHref);
    }
  } else if (preset) {
    base = sandboxHref(preset);
  } else if (recipeId) {
    base = recipeHref(recipeId, opts?.recipeChatHref);
  } else if (ability) {
    base = `/lab/${ability}`;
  } else {
    base = "/lab";
  }

  return appendReturnParams(base, opts);
}

export function buildMissionStepHref(
  step: TryItLike & { href?: string | null },
  opts?: TryItHrefOptions,
): string {
  return buildTryItHref(step, opts);
}
