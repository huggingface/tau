#!/usr/bin/env python3
"""Generate Tau's bundled model catalog from models.dev, following Pi."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

from tau_coding.catalog_loader import builtin_source_catalog
from tau_coding.models_dev import MODELS_DEV_URL, models_dev_catalog_document

DEFAULT_OUTPUT = Path("src/tau_coding/data/models-dev-catalog.json")
NVIDIA_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"
NVIDIA_UNSUPPORTED_MODELS = {
    "abacusai/dracarys-llama-3.1-70b-instruct",
    "bytedance/seed-oss-36b-instruct",
    "deepseek-ai/deepseek-v4-flash",
    "deepseek-ai/deepseek-v4-pro",
    "google/gemma-2-2b-it",
    "google/gemma-3n-e2b-it",
    "google/gemma-3n-e4b-it",
    "google/gemma-4-31b-it",
    "meta/llama-3.2-1b-instruct",
    "meta/llama-4-maverick-17b-128e-instruct",
    "microsoft/phi-4-mini-instruct",
    "minimaxai/minimax-m2.7",
    "mistralai/mistral-nemotron",
    "nvidia/nemotron-mini-4b-instruct",
    "qwen/qwen3-next-80b-a3b-instruct",
    "qwen/qwen3.5-397b-a17b",
    "sarvamai/sarvam-m",
    "upstage/solar-10.7b-instruct",
}


def _load_source(location: str) -> object:
    if location.startswith(("http://", "https://")):
        request = Request(location, headers={"User-Agent": "tau-model-catalog-generator"})
        with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit build input
            return json.load(response)
    with Path(location).open(encoding="utf-8") as source_file:
        return json.load(source_file)


def _nvidia_model_filter(source: object, live_source: object) -> set[str]:
    if not isinstance(source, dict) or not isinstance(live_source, dict):
        raise ValueError("NVIDIA filtering sources must be JSON objects")
    provider = source.get("nvidia")
    source_models = provider.get("models") if isinstance(provider, dict) else None
    live_models = live_source.get("data")
    if not isinstance(source_models, dict) or not isinstance(live_models, list):
        raise ValueError("NVIDIA filtering sources have invalid model data")
    live_ids = {
        model["id"].lower().replace("_", ".")
        for model in live_models
        if isinstance(model, dict) and isinstance(model.get("id"), str)
    }
    return {
        model_id
        for model_id in source_models
        if model_id.lower().replace("_", ".") in live_ids
        and model_id not in NVIDIA_UNSUPPORTED_MODELS
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=MODELS_DEV_URL,
        help="models.dev api.json URL or a local JSON fixture",
    )
    parser.add_argument(
        "--nvidia-source",
        default=NVIDIA_MODELS_URL,
        help="NVIDIA /models URL or local JSON fixture used by Pi's live filter",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"generated JSON path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    source = _load_source(args.source)
    document = models_dev_catalog_document(
        source,
        builtin_source_catalog(),
        provider_model_filters={
            "nvidia": _nvidia_model_filter(source, _load_source(args.nvidia_source))
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    providers = document["providers"]
    model_count = sum(len(provider["models"]) for provider in providers.values())
    print(f"Wrote {model_count} models for {len(providers)} providers to {args.output}")


if __name__ == "__main__":
    main()
