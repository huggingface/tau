import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import { unified } from "@astrojs/markdown-remark";
import { admonitions } from "./src/admonitions.mjs";

export default defineConfig({
  site: "https://twotimespi.dev",
  trailingSlash: "always",
  publicDir: "./static",
  integrations: [sitemap()],
  markdown: {
    shikiConfig: { theme: "min-light" },
    processor: unified({ remarkPlugins: [admonitions] }),
  },
});
