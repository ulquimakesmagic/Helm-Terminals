import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Production serves the built app with `vite preview` (see package.json "start"), so the security headers live here.
// The CSP is report-only for now: wallet connectors (TON Connect, NEAR Connect) load wallet frames and bridges from
// many origins, so it should be watched in the browser console before it's enforced.
const securityHeaders = {
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
  "Content-Security-Policy-Report-Only": [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob: https:",
    "connect-src 'self' https: wss:",
    "frame-src https:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ].join("; "),
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  preview: { allowedHosts: true, headers: securityHeaders },
});
