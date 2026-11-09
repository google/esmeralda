# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Catalog contract (``contracts/catalog.schema.json``) loading, YAML parsing and validation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SCHEMA = "esmeralda.catalog"
SCHEMA_VERSION = 1
SCHEMA_PATH = Path("contracts/catalog.schema.json")


class CatalogError(Exception):
    """The catalog could not be resolved; ``problems`` lists every issue found."""

    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("\n".join(self.problems))


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that rejects duplicate mapping keys (PyYAML keeps the last one silently)."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_yaml(path: Path, label: str) -> Any:
    """One YAML document from ``path``; duplicate keys and multi-document files are errors."""
    try:
        docs = list(yaml.load_all(path.read_text(), Loader=_UniqueKeyLoader))  # noqa: S506 (safe loader)
    except yaml.YAMLError as exc:
        raise CatalogError(f"{label}: invalid YAML: {exc}") from exc
    docs = [d for d in docs if d is not None]
    if len(docs) != 1:
        raise CatalogError(f"{label}: expected exactly one YAML document, found {len(docs)}")
    return docs[0]


@lru_cache(maxsize=8)
def _schema_cached(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def load_schema(root: Path) -> dict[str, Any]:
    path = root / SCHEMA_PATH
    if not path.is_file():
        raise CatalogError(f"{SCHEMA_PATH} not found under {root}")
    return _schema_cached(str(path))


def validate(schema: dict[str, Any], definition: str, instance: Any, label: str) -> list[str]:
    """Problems found validating ``instance`` against ``$defs/<definition>`` (empty when valid)."""
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - jsonschema ships with the locked dependency set
        raise CatalogError("the catalog resolver needs the 'jsonschema' package") from exc

    sub = {"$schema": schema.get("$schema"), "$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"}
    validator = jsonschema.Draft202012Validator(sub)
    problems = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: [str(p) for p in e.absolute_path]):
        where = "/".join(str(p) for p in err.absolute_path) or "(document)"
        # oneOf/anyOf errors are unreadable at the top: show the most specific sub-error.
        best = jsonschema.exceptions.best_match([err]) if err.context else err
        problems.append(f"{label}: {where}: {best.message}")
    return problems


def check_header(doc: Any, label: str) -> None:
    if not isinstance(doc, dict):
        raise CatalogError(f"{label}: expected a mapping at the top level")
    if doc.get("schema") != SCHEMA or doc.get("schema_version") != SCHEMA_VERSION:
        raise CatalogError(
            f"{label}: expected 'schema: {SCHEMA}' and 'schema_version: {SCHEMA_VERSION}' "
            f"(got schema={doc.get('schema')!r}, schema_version={doc.get('schema_version')!r})"
        )
