"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { HelperSearch } from "@/features/help/HelperSearch";
import { YONDER_CONSOLE } from "@/lib/brand";

export function AppShell({
  children,
  developmentMode = true,
}: {
  children: ReactNode;
  developmentMode?: boolean;
}) {
  const pathname = usePathname();

  return (
    <div className="app-shell">
      <div className="atmosphere" aria-hidden="true">
        <div className="atmosphere-hex" />
        <div className="atmosphere-orb atmosphere-orb-a" />
        <div className="atmosphere-orb atmosphere-orb-b" />
        <div className="atmosphere-vignette" />
        <div className="atmosphere-grain" />
      </div>

      {developmentMode ? (
        <div className="dev-banner" role="status">
          <span className="dev-banner-dot" aria-hidden="true" />
          Development security mode — mock principal active
        </div>
      ) : null}

      <header className="topbar">
        <Link href="/" className="brand" aria-label="Yonder home">
          <Image
            src="/yonder-logo.png"
            alt=""
            width={1024}
            height={281}
            className="brand-logo"
            priority
          />
          <span className="brand-sr">{YONDER_CONSOLE.name}</span>
        </Link>
        <HelperSearch />
        <nav className="nav" aria-label="Primary">
          {YONDER_CONSOLE.nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="nav-link"
              data-active={
                item.href === "/"
                  ? pathname === "/"
                    ? "true"
                    : "false"
                  : pathname === item.href || pathname.startsWith(`${item.href}/`)
                    ? "true"
                    : "false"
              }
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </header>

      <div className="shell-main">{children}</div>
    </div>
  );
}
