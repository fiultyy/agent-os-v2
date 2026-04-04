import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // BFF proxy to backend services
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_URL || "http://localhost:8000"}/:path*`,
      },
    ];
  },
};

export default nextConfig;
