"""Contract snapshot: schema.json (and the TS generated from it) must match the pydantic models."""

import json

from inktutor_protocol.export import SCHEMA_PATH, build


def test_schema_snapshot_is_current():
    assert json.loads(SCHEMA_PATH.read_text()) == build(), "run `make protocol` and commit schema.json + TS"


def test_wire_unions_cover_every_message_type():
    defs = build()["$defs"]
    client = {d["$ref"].split("/")[-1] for d in defs["ClientMessage"]["oneOf"]}
    server = {d["$ref"].split("/")[-1] for d in defs["ServerMessage"]["oneOf"]}
    assert {"InkUpdate", "HintRequest", "QuestionAnswer", "GhostAction", "SessionUpdate"} <= client
    assert {"ReadingMsg", "FeedbackMsg", "StatusMsg", "SessionMsg"} <= server
