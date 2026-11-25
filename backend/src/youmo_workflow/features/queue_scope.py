"""Persistent publication-time boundary for new production candidates."""

from sqlalchemy import String, cast, func, select
from sqlalchemy.sql.selectable import ScalarSelect

from youmo_workflow.storage.models import WorkflowSetting

CUTOFF_SETTING = "queue_cutoff"


def cutoff_stamp() -> ScalarSelect[str]:
    return (
        select(cast(func.json_extract(WorkflowSetting.value_json, "$.after"), String))
        .where(WorkflowSetting.id == CUTOFF_SETTING)
        .scalar_subquery()
    )
