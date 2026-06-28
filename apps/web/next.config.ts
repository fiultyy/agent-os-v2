import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // BFF proxy to backend services
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_URL || "http://localhost:8000/v1"}/:path*`,
      },
      // WebSocket passthrough to orchestrator (gateway has no WS proxy in Phase 0).
      // Frontend opens a same-origin ws://<host>/ws/canvas and Next rewrites it to orchestrator 8001.
      {
        source: "/ws/canvas",
        destination: `${process.env.ORCHESTRATOR_WS_URL || "http://localhost:8001"}/ws/canvas`,
        basePath: false,
      },
    ];
  },
};

export default nextConfig;
