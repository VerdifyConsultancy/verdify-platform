import { defineConfig } from "astro/config";

export default defineConfig({
  site: process.env.SITE_ORIGIN ?? "https://lab-stage.verdify.ai",
  output: "static",
  build: {
    format: "directory",
    inlineStylesheets: "never",
  },
  publicDir: ".generated/public",
  outDir: "dist",
  trailingSlash: "ignore",
  vite: {
    build: {
      sourcemap: false,
    },
  },
});
