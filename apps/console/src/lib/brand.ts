export const YONDER_CONSOLE = {
  name: "Yonder",
  tagline: "Intelligence beyond search.",
  nav: [
    { href: "/", label: "Chat" },
    { href: "/setup", label: "Get started" },
    { href: "/coder", label: "Coder" },
    { href: "/academy", label: "Academy" },
    { href: "/recipes", label: "Recipes" },
    { href: "/studio", label: "Studio" },
    { href: "/lab", label: "Lab" },
    { href: "/models", label: "Models" },
    { href: "/hardware", label: "Hardware" },
    { href: "/operations", label: "Operations" },
    { href: "/addons", label: "Add-ons" },
  ] as const,
};

export const GATEWAY_URL =
  process.env.NEXT_PUBLIC_YONDER_GATEWAY_URL ?? "http://127.0.0.1:8080";
