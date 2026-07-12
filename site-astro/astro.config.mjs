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
      // Keep font loads on the static origin. Vite otherwise inlines the
      // smallest KaTeX font as a data URL, which needlessly widens font-src.
      assetsInlineLimit: 0,
      sourcemap: false,
    },
  },
});
