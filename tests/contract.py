import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACT = Path(__file__).resolve().parents[1] / "contract" / "v1"
NAMES = ["check_in", "jobs", "upload_urls", "receipt", "job", "order", "fill"]


def _load(name: str) -> dict[str, Any]:
    with (CONTRACT / f"{name}.json").open() as handle:
        return json.load(handle)


SCHEMAS = {name: _load(name) for name in NAMES}
REGISTRY = Registry().with_resources(
    (f"{name}.json", Resource.from_contents(schema)) for name, schema in SCHEMAS.items()
)


def validator(name: str, pointer: str = "") -> Draft202012Validator:
    schema: dict[str, Any] = {"$ref": f"{name}.json{pointer}"}
    return Draft202012Validator(schema, registry=REGISTRY)


def check(name: str, instance: Any, pointer: str = "") -> None:
    validator(name, pointer).validate(instance)
