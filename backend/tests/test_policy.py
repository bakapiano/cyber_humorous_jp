import pytest
from pydantic import ValidationError

from youmo_workflow.domain.models import Policy


def test_invalid_trigger_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Policy(daily_trigger="25:99")


def test_duplicate_slots_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Policy(publish_slots=["13:00", "13:00", "19:00"])


def test_production_budget_is_consistent() -> None:
    with pytest.raises(ValidationError):
        Policy(candidate_limit=1, max_approved=3)


def test_publication_budget_is_consistent() -> None:
    with pytest.raises(ValidationError):
        Policy(publish_slots=["13:00"], max_new_publications_per_day=3)
