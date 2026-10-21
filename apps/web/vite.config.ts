import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.{test,spec}.?(c|m)[jt]s?(x)"],
    alias: [{ find: "@", replacement: path.resolve(__dirname, "./src") }],
    setupFiles: ["./src/test/setup.ts"],

  },
});
