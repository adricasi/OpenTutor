"""Export the protocol as one JSON schema (packages/protocol/schema.json); TS types are generated from it.

uv run python -m inktutor_protocol.export          # write schema.json
uv run python -m inktutor_protocol.export --check  # exit 1 if schema.json is stale (snapshot test)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from . import models as m

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.json"

MODELS: list[type[BaseModel]] = [
    m.BBox,
    m.Stroke,
    m.SymbolSpan,
    m.ReadingLine,
    m.GraphNode,
    m.GraphEdge,
    m.Graph,
    m.Reading,
    m.Anchor,
    m.FeedbackEvent,
    m.PageRef,
    m.Session,
    m.CheckResult,
    m.TutorInfo,
    m.InkUpdate,
    m.HintRequest,
    m.QuestionAnswer,
    m.GhostAction,
    m.MarkDismiss,
    m.SessionUpdate,
    m.VoiceAudio,
    m.VoiceStop,
    m.LineCorrection,
    m.VoiceDictate,
    m.SessionMsg,
    m.ReadingMsg,
    m.FeedbackMsg,
    m.StatusMsg,
    m.VoiceTranscript,
    m.VoiceAudioOut,
    m.ErrorMsg,
    m.RuntimeStatus,
    m.RuntimeMsg,
]


def _tidy(node: Any, in_properties: bool = False) -> Any:
    """Make the schema TS-friendly: no per-property titles (they become alias types in TS) and
    draft-07 tuples (`items: [...]`) instead of 2020-12 `prefixItems`, which json2ts doesn't read."""
    if isinstance(node, list):
        return [_tidy(x) for x in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k == "properties":
            out[k] = {pk: _tidy(pv, in_properties=True) for pk, pv in v.items()}
        elif k == "title" and in_properties:
            continue
        else:
            out[k] = _tidy(v)
    if "prefixItems" in out:
        items = out.pop("prefixItems")
        out |= {"items": items, "minItems": len(items), "maxItems": len(items)}
    return out


def build() -> dict[str, Any]:
    tidy: dict[str, Any] = _tidy(_build())
    return tidy


def _build() -> dict[str, Any]:
    defs: dict[str, Any] = {}
    for model in MODELS:
        s = model.model_json_schema(ref_template="#/$defs/{model}", mode="serialization")
        defs.update(s.pop("$defs", {}))
        defs[model.__name__] = s
    unions: tuple[tuple[str, Any], ...] = (("ClientMessage", m.ClientMessage), ("ServerMessage", m.ServerMessage))
    for name, union in unions:
        s = TypeAdapter[Any](union).json_schema(ref_template="#/$defs/{model}", mode="serialization")
        defs.update(s.pop("$defs", {}))
        s.pop("discriminator", None)
        defs[name] = {"title": name, **s}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "InkTutorProtocol",
        "type": "object",
        "properties": {k: {"$ref": f"#/$defs/{k}"} for k in sorted(defs)},
        "$defs": dict(sorted(defs.items())),
    }


def main() -> int:
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text() != text:
            print("schema.json is stale: run `make protocol`", file=sys.stderr)
            return 1
        return 0
    SCHEMA_PATH.write_text(text)
    print(f"wrote {SCHEMA_PATH} ({len(build()['$defs'])} types)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
