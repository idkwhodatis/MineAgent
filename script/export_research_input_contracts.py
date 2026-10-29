"""Exact unregistered audit DTO components; no endpoint or activation claim."""

import json
from pathlib import Path

from backend.app.research_input_routes import InputDerivationView, RetainedExternalInput


def export():
    components = {}
    for model in (InputDerivationView, RetainedExternalInput):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        definitions = schema.pop("$defs", {})
        definitions[model.__name__] = schema
        for name, value in definitions.items():
            if name in components and components[name] != value:
                raise ValueError("Conflicting exact audit DTO definitions")
            components[name] = value
    target = (
        Path(__file__).resolve().parents[1]
        / "packages/contracts/research-input-audit.json"
    )
    target.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {
                    "title": "Unregistered input derivation audit contract",
                    "version": "1",
                },
                "paths": {},
                "components": {"schemas": components},
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    return target


if __name__ == "__main__":
    print(export())
