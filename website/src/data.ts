import { readFileSync } from "node:fs";
import { parse as yaml } from "yaml";
import { parse as toml } from "smol-toml";

export const site = {
  title: "Tau",
  description:
    "An educational Python project for learning how coding agents are built.",
  githubUrl: "https://github.com/huggingface/tau",
  discordUrl: "https://link.alejandro-ao.com/tau-discord",
  editRepoUrl: "https://github.com/huggingface/tau/edit/main/website/content",
  githubRepo: "huggingface/tau",
};
export const version = (
  toml(readFileSync("../pyproject.toml", "utf8")).project as { version: string }
).version;
export interface Link {
  label: string;
  path: string;
}
export interface Group {
  label: string;
  link?: string;
  path?: string;
  items?: Link[];
}
export const sidebar = yaml(
  readFileSync("data/sidebar.yaml", "utf8"),
) as Group[];
export interface Phase {
  id: string | number;
  status: string;
  title: string;
  summary: string;
}
export const groups = yaml(readFileSync("data/roadmap.yaml", "utf8"))
  .groups as { label: string; note: string; phases: Phase[] }[];
export const releases = JSON.parse(
  readFileSync("../src/tau_coding/data/release-notes/releases.json", "utf8"),
) as { version: string; date: string; sections: Record<string, string[]> }[];
export function pagePath(id: string) {
  return id === "_index" ? "/" : `/${id.replace(/\/_index$/, "")}/`;
}
