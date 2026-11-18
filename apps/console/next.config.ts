import type { NextConfig } from "next";

const gatewayUrl =
  process.env.NEXT_PUBLIC_YONDER_GATEWAY_URL ?? "http://127.0.0.1:8080";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Optional same-origin proxy; browser may also call the gateway directly.
    return [
      {
        source: "/gateway/:path*",
        destination: `${gatewayUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
