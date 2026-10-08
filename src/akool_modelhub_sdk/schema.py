"""Conservative conversion of model form parameters; shared fixtures govern both SDKs."""

import hashlib
from typing import Any

import rfc8785

from .types import Model, ModelSchema


def convert_schema(model: Model) -> ModelSchema:
    warnings: list[str] = []

    def object_schema(parameters: list[dict[str, Any]]) -> dict[str, Any]:
        props, required = {}, []
        for p in parameters:
            key, kind = p.get("key"), p.get("type")
            if not isinstance(key, str):
                warnings.append("Parameter without a key was omitted")
                continue
            out: dict[str, Any] = {}
            if kind in ("text", "textarea"):
                out = {"type": "string"}
                if p.get("max_length") is not None:
                    out["maxLength"] = p["max_length"]
            elif kind in ("number", "slider"):
                out = {"type": "number"}
                for source, target in (("min", "minimum"), ("max", "maximum")):
                    if p.get(source) is not None:
                        out[target] = p[source]
            elif kind == "boolean":
                out = {"type": "boolean"}
            elif kind == "select" and p.get("options"):
                out = {"enum": [option["value"] for option in p["options"]]}
            elif isinstance(kind, str) and kind.endswith("_upload"):
                out = {"type": "string", "format": "uri"}
            elif isinstance(kind, str) and kind.endswith("_upload_group"):
                out = {"type": "array", "items": {"type": "string", "format": "uri"}}
            elif kind == "object":
                out = object_schema(p.get("object_properties") or [])
            elif kind == "array":
                item_type = p.get("items_type")
                items = (
                    object_schema(p.get("object_properties") or [])
                    if item_type == "object"
                    else ({"type": item_type} if item_type in ("string", "number") else {})
                )
                out = {"type": "array", "items": items}
            else:
                warnings.append(f"Unsupported parameter type for {key}: {kind}")
                out = {"x-model-hub-parameter": p}
            if out.get("type") == "array":
                for source, target in (("min_count", "minItems"), ("max_count", "maxItems")):
                    if p.get(source) is not None:
                        out[target] = p[source]
            for source, target in (("default", "default"), ("description", "description")):
                value = p.get(source)
                if source == "description" and isinstance(value, dict):
                    value = value.get("en") or value.get("zh")
                if value is not None:
                    out[target] = value
            if "example" in p and p["example"] is not None:
                out["examples"] = [p["example"]]
            condition = p.get("show_if")
            if condition:
                out["x-model-hub-show-if"] = condition
                warnings.append(
                    f"Conditional visibility for {key} is a hint, not a required constraint"
                )
            elif p.get("required"):
                required.append(key)
            props[key] = out
        result: dict[str, Any] = {
            "type": "object",
            "properties": props,
            "additionalProperties": True,
        }
        if required:
            result["required"] = required
        return result

    schema = object_schema(model.parameters)
    # Hash the JSON schema, not floating point representations in UI metadata.
    canonical = rfc8785.dumps(schema)
    return ModelSchema(
        model_id=model.model_id,
        input_schema=schema,
        output_schema=model.output_schema,
        warnings=warnings,
        schema_hash=hashlib.sha256(canonical).hexdigest(),
        request_id=model.request_id,
    )
