from pathlib import Path

import pytest

from opentutor.config import Settings
from opentutor.tutors import NoTutorsError, load_tutors

RUBRIC = "## teaching\nT\n\n## check\nC\n\n## practice\nP\n"


def make(root: Path, tutor_id: str, extra: str = "", rubric: str = RUBRIC) -> None:
    folder = root / tutor_id
    folder.mkdir()
    (folder / "tutor.toml").write_text(
        f'id = "{tutor_id}"\nname = "N"\ndescription = "D"\ntopics = ["a"]\n{extra}'
    )
    (folder / "rubric.md").write_text(rubric)


def test_ac_008_2_ships_maths_english_science() -> None:
    assert set(load_tutors(Settings().tutors_dir)) == {"maths", "english-language", "science"}


def test_ac_008_5_new_folder_is_offered(tmp_path: Path) -> None:
    make(tmp_path, "history")
    assert list(load_tutors(tmp_path)) == ["history"]


def test_ac_008_3_invalid_definition_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    make(tmp_path, "good")
    make(tmp_path, "unknown-field", extra="colour = 1\n")
    make(tmp_path, "no-rubric-section", rubric="## teaching\nT\n")
    assert list(load_tutors(tmp_path)) == ["good"]
    assert "colour" in caplog.text
    assert "practice" in caplog.text


def test_ac_008_6_disabled_tutor_hidden(tmp_path: Path) -> None:
    make(tmp_path, "on")
    make(tmp_path, "off", extra="enabled = false\n")
    assert list(load_tutors(tmp_path)) == ["on"]


def test_ac_008_4_no_valid_tutor_refuses_to_start(tmp_path: Path) -> None:
    make(tmp_path, "bad", extra="enabled = 3\n")
    with pytest.raises(NoTutorsError):
        load_tutors(tmp_path)
