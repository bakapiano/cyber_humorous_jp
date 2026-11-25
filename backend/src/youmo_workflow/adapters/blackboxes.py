from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import (
    PackagingOutput,
    PackagingRequest,
    SelectionOutput,
    SelectionRequest,
)


class UnimplementedSelection:
    def select(self, request: SelectionRequest) -> SelectionOutput:
        raise WorkflowError(
            "SELECTION_NOT_IMPLEMENTED",
            f"批次 {request.batch_id} 已冻结。首筛到二筛（含内容准备）为未接入黑盒。",
        )


class UnimplementedPackaging:
    def package(self, request: PackagingRequest) -> PackagingOutput:
        raise WorkflowError(
            "PACKAGING_NOT_IMPLEMENTED",
            f"内容 {request.content.content_id} 的标题／封面生成黑盒尚未接入。",
        )
