"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS: Array<{ href: string; label: string }> = [
  { href: "/academy", label: "Gallery" },
  { href: "/academy/missions", label: "Missions" },
  { href: "/academy/sandbox", label: "Practice" },
  { href: "/academy/cohorts", label: "Cohorts" },
  { href: "/academy/capacity", label: "Capacity" },
  { href: "/academy/prompts", label: "Prompts" },
  { href: "/academy/workbench", label: "Workbench" },
  { href: "/academy/instructor", label: "Instructor" },
];

export function AcademySubnav() {
  const pathname = usePathname() || "";

  return (
    <nav className="academy-subnav" aria-label="Academy sections">
      <div className="academy-subnav-head">
        <p className="academy-subnav-kicker">Academy</p>
        <p className="academy-subnav-hint">Choose a workspace</p>
      </div>
      <div className="academy-subnav-track" role="list">
        {LINKS.map((link) => {
          const active =
            link.href === "/academy"
              ? pathname === "/academy"
              : pathname === link.href || pathname.startsWith(`${link.href}/`);
          return (
            <Link
              key={link.href}
              href={link.href}
              role="listitem"
              className={
                active
                  ? "academy-subnav-link academy-subnav-active"
                  : "academy-subnav-link"
              }
              aria-current={active ? "page" : undefined}
            >
              <span className="academy-subnav-link-label">{link.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
