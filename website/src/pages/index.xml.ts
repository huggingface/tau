import rss from "@astrojs/rss";
import { getCollection } from "astro:content";
import { pagePath, site } from "../data";
import type { APIContext } from "astro";

export async function GET(context: APIContext) {
  return rss({
    title: site.title,
    description: site.description,
    site: context.site!,
    items: (await getCollection("docs"))
      .filter((entry) => !entry.id.endsWith("_index"))
      .map((entry) => ({
        title: entry.data.title,
        description: entry.data.description,
        link: pagePath(entry.id),
      })),
  });
}
