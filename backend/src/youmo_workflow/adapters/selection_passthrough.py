from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import SelectionOutput
from youmo_workflow.domain.selection import InitialOutput


class PassthroughRescreen:
    def review(self, request: InitialOutput) -> InitialOutput:
        return request


class UnimplementedContentPreparation:
    def prepare(self, request: InitialOutput) -> SelectionOutput:
        raise WorkflowError(
            "CONTENT_PREPARATION_NOT_IMPLEMENTED",
            f"初筛与复筛直通已保留 {len(request.shortlisted_ids)} 条候选；下载、识别及翻译产物尚未接入。",
        )
