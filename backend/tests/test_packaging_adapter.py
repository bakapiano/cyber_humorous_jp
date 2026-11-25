from pathlib import Path

import pytest
from production_support import cover_plan

from youmo_workflow.adapters.packaging_parse import parse_planning
from youmo_workflow.adapters.production_files import encode
from youmo_workflow.domain.errors import WorkflowError


def test_original_v003b_support_notes_are_preserved(tmp_path: Path) -> None:
    value = cover_plan()
    titles = value["titles"]
    assert isinstance(titles, list)
    for row in titles:
        assert isinstance(row, dict)
        evidence = row["evidence"]
        assert isinstance(evidence, list) and isinstance(evidence[0], dict)
        evidence[0]["support_note"] = "原版已有的引文支持说明"
        row["title"] = row.pop("text")
        row.pop("title_id")
    plan = parse_planning(encode(value), tmp_path)
    assert plan.titles[0].title_id == "TITLE1"
    assert plan.titles[0].evidence[0].support_note == "原版已有的引文支持说明"


def test_title_alias_conflicts_are_not_silently_repaired(tmp_path: Path) -> None:
    value = cover_plan()
    titles = value["titles"]
    assert isinstance(titles, list) and isinstance(titles[0], dict)
    titles[0]["id"] = "TITLE2"
    with pytest.raises(WorkflowError):
        parse_planning(encode(value), tmp_path)
