/** Shared help glossary types. */

export interface GlossaryLink {
  label: string;
  href: string;
}

export interface GlossaryTerm {
  id: string;
  term: string;
  aliases: string[];
  short: string;
  body: string;
  related: string[];
  links: GlossaryLink[];
}

export interface AppHit {
  id: string;
  label: string;
  href: string;
  keywords: string[];
  section: "In app";
}
