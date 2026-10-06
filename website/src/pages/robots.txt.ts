import type { APIContext } from "astro";

export function GET({ site }: APIContext) {
  return new Response(
    `User-agent: *\nAllow: /\nSitemap: ${new URL("sitemap-index.xml", site)}\n`,
  );
}
