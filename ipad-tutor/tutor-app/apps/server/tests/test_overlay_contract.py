"""The Swift helper's Codable structs must match the protocol (schema.json), so neither side can drift silently.

- every `// protocol: <Name>` struct in apps/overlay/Sources/OverlayCore/Protocol.swift uses only the schema's
  field names (its CodingKeys), and covers the required ones; non-optional Swift fields are never null on the wire;
- every `// protocol-enum: <Model>.<field>` enum has exactly the schema's values;
- the shared wire examples (apps/overlay/Fixtures/screen-messages.json, which overlay-selftest decodes and
  re-encodes on the Swift side) are valid pydantic messages, and the server examples are exactly what the server
  sends (model_dump, nulls included).
"""

import json
import re

import pytest
from inktutor_protocol import ScreenClientMessage, ScreenServerMessage
from inktutor_protocol.export import SCHEMA_PATH
from inktutor_server.config import APP_ROOT
from pydantic import TypeAdapter

OVERLAY = APP_ROOT / "apps" / "overlay"
SWIFT = OVERLAY / "Sources" / "OverlayCore" / "Protocol.swift"
EXAMPLES = OVERLAY / "Fixtures" / "screen-messages.json"
DEFS = json.loads(SCHEMA_PATH.read_text())["$defs"]
# what the helper encodes (the rest it decodes)
SENT_BY_HELPER = {"ScreenHello", "GuideGoal", "GuideControl", "SessionUpdate", "DisplayInfo", "BBox"}


def _block(src: str, start: int) -> str:
    """The `{ … }` body that starts at the first brace after `start`."""
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[i + 1 : j]
    raise AssertionError("unbalanced braces in Protocol.swift")


def _cases(body: str) -> dict[str, str]:
    """Swift enum cases → raw values (`case a, b`, `case fromPoint = "from_point"`, `` case `repeat` ``)."""
    out: dict[str, str] = {}
    for line in re.findall(r"^\s*case\s+(.+)$", body, re.M):
        for part in line.split(","):
            name, _, raw = part.partition("=")
            name = name.strip().strip("`")
            out[name] = raw.strip().strip('"') if raw else name
    return out


def swift_structs() -> dict[str, dict[str, tuple[str, bool]]]:
    """{protocol name: {json key: (swift type, optional)}}."""
    src = SWIFT.read_text()
    out: dict[str, dict[str, tuple[str, bool]]] = {}
    for m in re.finditer(r"// protocol: (\w+)\n(?:///.*\n)*public struct (\w+)", src):
        name, struct = m.groups()
        assert name == struct, f"// protocol: {name} labels struct {struct}"
        body = re.sub(r"//.*", "", _block(src, m.end()))  # drop comments
        keys_body = _block(body, body.index("enum CodingKeys"))
        keys = _cases(keys_body)
        props = dict(re.findall(r"^\s*public var (\w+)(?::\s*([^=\n]+?))?\s*(?:=.*)?$", body, re.M))
        assert set(props) == set(keys), f"{name}: properties {sorted(props)} vs CodingKeys {sorted(keys)}"
        out[name] = {keys[p]: (t.strip() or "String", t.strip().endswith("?")) for p, t in props.items()}
    return out


def swift_enums() -> dict[str, list[str]]:
    src = SWIFT.read_text()
    out = {}
    for m in re.finditer(r"// protocol-enum: (\w+\.\w+)\npublic enum \w+", src):
        out[m.group(1)] = sorted(_cases(_block(src, m.end())).values())
    return out


def _nullable(prop: dict) -> bool:
    return any(alt.get("type") == "null" for alt in prop.get("anyOf", []))


def _enum(prop: dict) -> list[str]:
    for alt in [prop, *prop.get("anyOf", [])]:
        if "enum" in alt:
            return sorted(alt["enum"])
        if "const" in alt:
            return [alt["const"]]
    raise AssertionError(f"no enum in {prop}")


STRUCTS = swift_structs()


def test_swift_mirrors_every_screen_message_and_type():
    members = {
        ref["$ref"].split("/")[-1]
        for union in ("ScreenClientMessage", "ScreenServerMessage")
        for ref in DEFS[union]["oneOf"]
    }
    assert members | {"OverlayEvent", "BBox", "Point2", "DisplayInfo"} <= set(STRUCTS)


@pytest.mark.parametrize("name", sorted(STRUCTS))
def test_swift_struct_matches_schema(name):
    schema = DEFS[name]
    props, required = schema["properties"], set(schema.get("required", []))
    swift = STRUCTS[name]
    unknown = set(swift) - set(props)
    assert not unknown, f"{name}: Swift sends/reads keys the protocol doesn't have: {unknown}"
    for key, (_, optional) in swift.items():
        if not optional:
            assert not _nullable(props[key]), f"{name}.{key} can be null on the wire: make it optional in Swift"
    if name in SENT_BY_HELPER:
        missing = {k for k in required if k not in swift or swift[k][1]}
        assert not missing, f"{name}: the helper must always send {missing}"
    else:  # the helper decodes it: everything the server always sends must be there or optional
        missing = required - set(swift)
        assert not missing, f"{name}: Swift can't decode required {missing}"
    if "type" in props and "type" in swift and "const" in props["type"]:
        src = SWIFT.read_text()
        assert f'public var type = "{props["type"]["const"]}"' in src, f"{name}: Swift type tag"


@pytest.mark.parametrize(("where", "values"), sorted(swift_enums().items()))
def test_swift_enum_matches_schema(where, values):
    model, field = where.split(".")
    assert values == _enum(DEFS[model]["properties"][field]), where


def test_every_screen_enum_is_mirrored():
    assert {"OverlayEvent.type", "OverlayEvent.state", "GuideControl.action"} <= set(swift_enums())


# ---- the shared wire examples ----------------------------------------------------
EX = json.loads(EXAMPLES.read_text())


@pytest.mark.parametrize("i", range(len(EX["server"])))
def test_server_example_is_what_the_server_sends(i):
    ex = EX["server"][i]
    msg = TypeAdapter(ScreenServerMessage).validate_python(ex)
    assert msg.model_dump(mode="json") == ex, "regenerate this entry from the pydantic model"


@pytest.mark.parametrize("i", range(len(EX["client"])))
def test_client_example_is_accepted(i):
    ex = EX["client"][i]
    msg = TypeAdapter(ScreenClientMessage).validate_python(ex)
    assert msg.model_dump(mode="json", exclude_unset=True) | {"type": ex["type"]} == ex


def test_examples_cover_every_overlay_type():
    types = {m["event"]["type"] for m in EX["server"] if m["type"] == "overlay"}
    assert types == set(_enum(DEFS["OverlayEvent"]["properties"]["type"]))
