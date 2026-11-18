"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  buildHelperAiHref,
  matchAppHits,
  matchGlossaryTerms,
} from "./search";
import type { GlossaryTerm } from "./types";
import { findTerm, useGlossaryTerms } from "./useGlossary";

export function HelperSearch() {
  const router = useRouter();
  const inputId = useId();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<GlossaryTerm | null>(null);
  const { terms } = useGlossaryTerms();

  const termMatches = useMemo(
    () => matchGlossaryTerms(query, terms),
    [query, terms],
  );
  const appHits = useMemo(() => matchAppHits(query), [query]);
  const showPanel = open && (query.trim().length > 0 || selected);

  const close = useCallback(() => {
    setOpen(false);
    setSelected(null);
  }, []);

  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      const editable =
        tag === "input" ||
        tag === "textarea" ||
        target?.isContentEditable;
      if (event.key === "/" && !editable && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    function onPointer(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        close();
      }
    }
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [close]);

  function askAi() {
    const href = buildHelperAiHref(query);
    close();
    router.push(href);
  }

  function onInputKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      inputRef.current?.blur();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      askAi();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const top = termMatches[0]?.term;
      if (top) {
        setSelected(top);
        setOpen(true);
      } else if (appHits[0]) {
        close();
        router.push(appHits[0].href);
      }
    }
  }

  return (
    <div className="helper-search" ref={rootRef}>
      <div className="helper-search-row">
        <label className="brand-sr" htmlFor={inputId}>
          Helper search
        </label>
        <input
          id={inputId}
          ref={inputRef}
          className="helper-search-input"
          type="search"
          value={query}
          placeholder="Search terms… (/ )"
          autoComplete="off"
          onChange={(event) => {
            setQuery(event.target.value);
            setSelected(null);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onInputKeyDown}
        />
        <button
          type="button"
          className="btn helper-ai-btn"
          onClick={askAi}
          title="Send to Chat (⌘/Ctrl+Enter)"
        >
          AI
        </button>
      </div>

      {showPanel ? (
        <div className="helper-search-panel" role="listbox">
          {selected ? (
            <div className="helper-definition">
              <div className="lab-card-kicker">{selected.term}</div>
              <p className="helper-definition-short">{selected.short}</p>
              <p>{selected.body}</p>
              {selected.related.length ? (
                <p className="helper-related">
                  Related:{" "}
                  {selected.related.map((id) => {
                    const rel = findTerm(terms, id);
                    if (!rel) return null;
                    return (
                      <button
                        key={id}
                        type="button"
                        className="helper-related-link"
                        onClick={() => setSelected(rel)}
                      >
                        {rel.term}
                      </button>
                    );
                  })}
                </p>
              ) : null}
              <div className="lab-card-actions">
                {selected.links.map((link) => (
                  <Link
                    key={link.href}
                    className="btn btn-ghost"
                    href={link.href}
                    onClick={close}
                  >
                    {link.label}
                  </Link>
                ))}
                <Link
                  className="btn btn-ghost"
                  href={`/help#${selected.id}`}
                  onClick={close}
                >
                  Open glossary
                </Link>
                <button type="button" className="btn btn-ghost" onClick={askAi}>
                  Ask AI
                </button>
              </div>
            </div>
          ) : (
            <>
              {termMatches.length ? (
                <section>
                  <div className="helper-section-label">Terms</div>
                  <ul className="helper-result-list">
                    {termMatches.map(({ term, reason }) => (
                      <li key={term.id}>
                        <button
                          type="button"
                          className="helper-result-btn"
                          onClick={() => setSelected(term)}
                        >
                          <strong>{term.term}</strong>
                          <span>{term.short}</span>
                          <em>{reason}</em>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
              {appHits.length ? (
                <section>
                  <div className="helper-section-label">In app</div>
                  <ul className="helper-result-list">
                    {appHits.map((hit) => (
                      <li key={hit.id}>
                        <Link
                          className="helper-result-btn"
                          href={hit.href}
                          onClick={close}
                        >
                          <strong>{hit.label}</strong>
                          <span>{hit.href}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
              {!termMatches.length && !appHits.length ? (
                <p className="helper-empty">
                  No helper hits. Press <strong>AI</strong> to ask Chat, or open{" "}
                  <Link href="/help" onClick={close}>
                    the glossary
                  </Link>
                  .
                </p>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
