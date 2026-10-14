import { defineCollection } from "astro:content";
import { z } from "astro/zod";
import { glob } from "astro/loaders";

export const collections = {
  docs: defineCollection({
    loader: glob({ pattern: "**/*.md", base: "./content" }),
    schema: z.object({
      title: z.string(),
      description: z.string().optional(),
      image: z.string().optional(),
      mathjax: z.boolean().optional(),
      layout: z.string().optional(),
    }),
  }),
};
