import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/static/transcription/",
  build: {
    outDir: "../../static/transcription",
    emptyOutDir: true,
  },
});
