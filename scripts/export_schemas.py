"""Export JSON Schemas for every contract model to docs/schemas/.

    python scripts/export_schemas.py           # write
    python scripts/export_schemas.py --check   # exit 1 if docs/schemas is stale

Writes one file per model plus contract.bundle.json (all models under $defs; the
frontend generates TypeScript types from the bundle). LF line endings always.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic.json_schema import models_json_schema  # noqa: E402

from backend.contract.constants import CONTRACT_VERSION  # noqa: E402
from backend.contract.models import ALL_MODELS  # noqa: E402

SCHEMA_DIR = ROOT / "docs" / "schemas"
BUNDLE = "contract.bundle.json"


def _dump(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render() -> dict[str, str]:
    """Return {filename: content} for everything that belongs in docs/schemas."""
    out: dict[str, str] = {}
    for model in ALL_MODELS:
        out[f"{model.__name__}.json"] = _dump(model.model_json_schema())

    _, defs = models_json_schema(
        [(m, "validation") for m in ALL_MODELS], ref_template="#/$defs/{model}"
    )
    bundle = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Contract",
        "description": f"AI traffic lights contract v{CONTRACT_VERSION} (generated, do not edit)",
        "type": "object",
        "properties": {m.__name__: {"$ref": f"#/$defs/{m.__name__}"} for m in ALL_MODELS},
        "additionalProperties": False,
        "$defs": defs["$defs"],
    }
    out[BUNDLE] = _dump(bundle)
    return out


def stale_files() -> list[str]:
    expected = render()
    problems: list[str] = []
    for name, content in expected.items():
        p = SCHEMA_DIR / name
        if not p.exists():
            problems.append(f"missing: {name}")
        elif p.read_bytes().decode("utf-8") != content:
            problems.append(f"stale: {name}")
    if SCHEMA_DIR.exists():
        for p in SCHEMA_DIR.glob("*.json"):
            if p.name not in expected:
                problems.append(f"unexpected: {p.name}")
    return problems


def write() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    expected = render()
    for p in SCHEMA_DIR.glob("*.json"):
        if p.name not in expected:
            p.unlink()
    for name, content in expected.items():
        (SCHEMA_DIR / name).write_bytes(content.encode("utf-8"))  # bytes => LF on every OS
    print(f"wrote {len(expected)} schema files to {SCHEMA_DIR.relative_to(ROOT)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if docs/schemas is out of date")
    args = ap.parse_args()
    if args.check:
        problems = stale_files()
        if problems:
            print("docs/schemas is out of date; run: python scripts/export_schemas.py")
            print("\n".join("  " + p for p in problems))
            return 1
        print("docs/schemas is up to date")
        return 0
    write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
