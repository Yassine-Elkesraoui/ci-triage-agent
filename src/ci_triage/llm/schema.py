"""The Diagnosis JSON schema in the plain form both backends accept for constrained output.

Pydantic's schema uses ``$defs``/``$ref`` and ``anyOf: [X, null]``; here references are inlined,
optional fields become ``type: [X, "null"]`` and ``title``/``default`` are dropped. Lengths and ranges
stay (Gemini ignores ``maxLength``; Pydantic checks everything afterwards anyway).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_DROP = {"title", "default"}


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        extra = {k: _inline(v, defs) for k, v in node.items() if k != "$ref" and k not in _DROP}
        return {**target, **extra}
    out = {k: _inline(v, defs) for k, v in node.items() if k not in _DROP and k != "$defs"}
    options = out.get("anyOf")
    if isinstance(options, list) and len(options) == 2 and {"type": "null"} in options:
        other = next(option for option in options if option != {"type": "null"})
        if isinstance(other.get("type"), str):
            out.pop("anyOf")
            out = {**other, **out, "type": [other["type"], "null"]}
    return out


def response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """``model``'s schema, flattened, with every top-level field required (nullable ones may be null), so
    constrained decoding always writes every field."""
    schema = model.model_json_schema()
    flat = _inline(schema, schema.get("$defs", {}))
    flat["required"] = list(flat["properties"])
    return flat
