import path from 'path';
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: false,
  output: 'standalone',
  outputFileTracingRoot: path.join(__dirname, '../..'),
  typescript: {
    ignoreBuildErrors: true,
  },
};

export default nextConfig;
