import type { ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import "highlight.js/styles/atom-one-dark.css";
import "./globals.css";

export const metadata = {
  title: "Yonder",
  description: "Intelligence beyond search.",
  icons: {
    icon: [{ url: "/yonder-icon.png", type: "image/png" }],
    apple: [{ url: "/yonder-icon.png", type: "image/png" }],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  // Fonts are local/system stacks in globals.css — no CDN (air-gapped console).
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
