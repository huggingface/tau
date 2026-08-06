#!/usr/bin/env python3
"""Run and analyse a reproducible Pi-versus-Tau prompt-cache benchmark.

The benchmark deliberately gives both CLIs the same read-only prompt and model
configuration.  It stores each CLI's native JSONL session transcript and emits
request-level cache data as CSV and an SVG plot.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

MODEL = "gpt-5.6-luna"
PROVIDER = "openai-codex"
REPETITIONS = 3
PROMPT = """Explore this repository in read-only mode. Do not write, edit, delete,
rename, or execute commands that could change files. Use several read-only tool
calls (at least: list the top-level tree, inspect pyproject.toml, read AGENTS.md,
inspect the architecture docs, and trace the CLI/provider/session modules).
Then give a concise architecture summary covering tau_ai, tau_agent, tau_coding,
session persistence, and the main Pi-inspired separation of concerns. Cite the
paths you inspected. Stop after the summary."""


def run(argv: list[str], *, cwd: Path, env: dict[str, str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        result = subprocess.run(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit(f"benchmark command failed ({result.returncode}): {' '.join(argv)}")


def copy_tau_config(home: Path) -> None:
    """Copy only Tau's provider credentials/settings into an isolated HOME."""
    source = Path.home() / ".tau"
    target = home / ".tau"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("credentials.json", "providers.json", "catalog.toml"):
        path = source / name
        if path.exists():
            shutil.copy2(path, target / name)
    settings = target / "providers.json"
    if settings.exists():
        data = json.loads(settings.read_text(encoding="utf-8"))
        preferences = data.get("provider_preferences", {}).get(PROVIDER)
        if preferences is not None:
            defaults = preferences.setdefault("thinking_defaults", {})
            defaults[MODEL] = "medium"
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def tau_session_path(tau_home: Path, session_id: str, cwd: Path) -> Path:
    del cwd  # Tau's slug is an internal detail; the unique session id is stable.
    candidates = list((tau_home / ".tau" / "sessions").rglob(f"{session_id}.jsonl"))
    return candidates[0] if candidates else tau_home / ".tau" / "sessions" / f"{session_id}.jsonl"


def usage_rows(path: Path, cli: str, repetition: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message", {})
        if message.get("role") != "assistant":
            continue
        usage = message.get("usage", {})
        if not usage:
            continue
        prompt = int(usage.get("input", usage.get("inputTokens", 0)) or 0)
        cached = int(usage.get("cache_read", usage.get("cacheRead", 0)) or 0)
        written = int(usage.get("cache_write", usage.get("cacheWrite", 0)) or 0)
        total = prompt + cached + written
        rows.append({"cli": cli, "repetition": repetition, "request": len(rows) + 1,
                     "input_tokens": prompt, "cached_input_tokens": cached,
                     "cache_write_tokens": written, "prompt_tokens": total,
                     "cache_hit_rate": cached / total if total else None})
    return rows


def make_plot(csv_path: Path, output: Path) -> None:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = 900, 520
    left, top, plot_width, plot_height = 80, 45, 770, 390
    colors = {"tau": "#2563eb", "pi": "#dc2626"}
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="450" y="25" text-anchor="middle" font-family="sans-serif" '
        'font-size="18" font-weight="bold">Prompt cache hit rate per request</text>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" '
        f'y2="{top + plot_height}" stroke="#333"/>',
        '<text x="20" y="250" transform="rotate(-90 20 250)" text-anchor="middle" '
        'font-family="sans-serif" font-size="13">Cache hit rate (%)</text>',
        '<text x="465" y="490" text-anchor="middle" font-family="sans-serif" '
        'font-size="13">Model request in session</text>',
    ]
    for tick in range(0, 101, 20):
        y = top + plot_height - tick / 100 * plot_height
        lines += [
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" y2="{y:.1f}" '
            'stroke="#ddd"/>',
            f'<text x="70" y="{y + 4:.1f}" text-anchor="end" font-family="sans-serif" '
            f'font-size="11">{tick}</text>',
        ]
    legend_y = 65
    for index, cli in enumerate(("tau", "pi")):
        subset = [r for r in rows if r["cli"] == cli]
        for repetition in range(1, REPETITIONS + 1):
            values = [
                float(r["cache_hit_rate"]) * 100
                for r in subset
                if int(r["repetition"]) == repetition and r["cache_hit_rate"] != ""
            ]
            if not values:
                continue
            points = " ".join(
                f"{left + (request - 1) * 55:.1f},"
                f"{top + plot_height - value / 100 * plot_height:.1f}"
                for request, value in enumerate(values, 1)
            )
            lines.append(
                f'<polyline points="{points}" fill="none" stroke="{colors[cli]}" '
                f'stroke-width="2" opacity="{0.45 + repetition * 0.15:.2f}"/>'
            )
        x = 650 + index * 90
        lines += [
            f'<line x1="{x}" y1="{legend_y}" x2="{x + 20}" y2="{legend_y}" '
            f'stroke="{colors[cli]}" stroke-width="3"/>',
            f'<text x="{x + 26}" y="{legend_y + 4}" font-family="sans-serif" '
            f'font-size="12">{cli}</text>',
        ]
    lines.append("</svg>")
    output.with_suffix(".svg").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="run all six CLI sessions")
    parser.add_argument("--target", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("benchmark-results/cache"))
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "prompt.txt").write_text(PROMPT + "\n", encoding="utf-8")
    if args.run:
        for cli in ("tau", "pi"):
            for repetition in range(1, REPETITIONS + 1):
                home = root / f"{cli}-home-r{repetition}"
                home.mkdir(parents=True, exist_ok=True)
                env = os.environ.copy()
                if cli == "tau":
                    copy_tau_config(home)
                    env["HOME"] = str(home)
                    session_id = f"cache-benchmark-tau-r{repetition}"
                    command = ["uv", "run", "tau", "--print", "--mode", "transcript",
                               "--provider", PROVIDER, "--model", MODEL,
                               "--session-id", session_id, PROMPT]
                    transcript = root / "transcripts" / f"tau-r{repetition}.stdout.txt"
                else:
                    session_dir = home / "sessions"
                    env["PI_CODING_AGENT_DIR"] = str(home / "agent")
                    env["PI_CODING_AGENT_SESSION_DIR"] = str(session_dir)
                    pi_agent = Path.home() / ".pi" / "agent"
                    (home / "agent").mkdir(parents=True, exist_ok=True)
                    if (pi_agent / "auth.json").exists():
                        shutil.copy2(pi_agent / "auth.json", home / "agent" / "auth.json")
                    command = ["pi", "--print", "--provider", PROVIDER, "--model", MODEL,
                               "--thinking", "medium", "--tools", "read,grep,find,ls",
                               "--session-dir", str(session_dir), PROMPT]
                    transcript = root / "transcripts" / f"pi-r{repetition}.stdout.txt"
                run(command, cwd=args.target, env=env, output=transcript)
    rows: list[dict[str, Any]] = []
    for cli in ("tau", "pi"):
        for repetition in range(1, REPETITIONS + 1):
            if cli == "tau":
                path = tau_session_path(
                    root / f"{cli}-home-r{repetition}",
                    f"cache-benchmark-tau-r{repetition}",
                    args.target,
                )
            else:
                candidates = sorted(
                    (root / f"{cli}-home-r{repetition}" / "sessions").glob("*.jsonl")
                )
                if not candidates:
                    continue
                path = candidates[-1]
            if path.exists():
                rows.extend(usage_rows(path, cli, repetition))
    csv_path = root / "request-cache.csv"
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        make_plot(csv_path, root / "request-cache.png")
    print(root)


if __name__ == "__main__":
    main()
