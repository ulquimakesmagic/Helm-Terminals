// Wallet bridges use independently operated HTTPS/WSS endpoints. Scripts remain same-origin.
export const securityHeaders = {
  "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "Content-Security-Policy": ["default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'",
    "font-src 'self'", "img-src 'self' data: blob: https:", "connect-src 'self' https: wss:", "frame-src https:",
    "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'", "object-src 'none'"].join("; "),
};
