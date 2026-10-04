import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Production uses server.mjs; preview is a local build-inspection tool only.
export default defineConfig({plugins: [react()], server: {port: 5173}});
