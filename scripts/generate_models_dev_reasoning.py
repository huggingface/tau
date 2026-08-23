#!/usr/bin/env python3
"""Generate Tau's bundled per-model reasoning maps from models.dev."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

from tau_coding.catalog_loader import builtin_catalog
from tau_coding.models_dev import MODELS_DEV_URL, reasoning_maps_document

DEFAULT_OUTPUT = Path("src/tau_coding/data/models-dev-reasoning.json")


def _load_source(location: str) -> object:
    if location.startswith(("http://", "https://")):
        request = Request(location, headers={"User-Agent": "tau-model-catalog-generator"})
        with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit build input
            return json.load(response)
    with Path(location).open(encoding="utf-8") as source_file:
        return json.load(source_file)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=MODELS_DEV_URL,
        help="models.dev api.json URL or a local JSON fixture",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"generated JSON path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    document = reasoning_maps_document(_load_source(args.source), builtin_catalog())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    providers = document["providers"]
    model_count = sum(len(models) for models in providers.values())
    print(f"Wrote {model_count} reasoning maps for {len(providers)} providers to {args.output}")


if __name__ == "__main__":
    main()
