import re
from collections.abc import Callable
from pathlib import Path

from youmo_workflow.adapters.selection_files import read_model, sha256
from youmo_workflow.adapters.selection_models import FrozenInitialCall, SavedCompletion
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.selection import InitialRequest


def atomic_save(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


class SelectionJournal:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def folder(self, batch_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id):
            raise WorkflowError("SELECTION_BATCH_INVALID", "初筛批次标识无效。")
        return self.directory / batch_id

    def freeze(
        self, request: InitialRequest, prepare: Callable[[InitialRequest], FrozenInitialCall]
    ) -> FrozenInitialCall:
        folder = self.folder(request.batch_id)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / "request.json"
        if not destination.exists():
            call = prepare(request)
            try:
                with destination.open("x", encoding="utf-8") as file:
                    file.write(call.model_dump_json())
            except FileExistsError:
                pass
        call = read_model(destination, FrozenInitialCall)
        if call.request_sha256 != sha256(request.model_dump_json().encode()):
            raise WorkflowError(
                "SELECTION_INPUT_CHANGED", "同批次已有不同的冻结输入，未重新调用模型。"
            )
        if call.prompt_sha256 != sha256(call.prompt.encode()):
            raise WorkflowError("SELECTION_INPUT_CHANGED", "冻结 prompt 哈希不匹配。")
        return call

    def cached(self, call: FrozenInitialCall) -> SavedCompletion | None:
        path = self.folder(call.request.batch_id) / "response.json"
        if not path.exists():
            return None
        result = read_model(path, SavedCompletion)
        if result.request_sha256 != call.request_sha256:
            raise WorkflowError("SELECTION_INPUT_CHANGED", "缓存响应与请求不匹配。")
        return result

    def execute(
        self,
        call: FrozenInitialCall,
        invoke: Callable[[], SavedCompletion],
        resume: Callable[[], SavedCompletion] | None = None,
    ) -> SavedCompletion:
        folder = self.folder(call.request.batch_id)
        try:
            with (folder / "attempt.json").open("x", encoding="utf-8") as file:
                file.write('{"max_calls":1,"request_sha256":"' + call.request_sha256 + '"}')
        except FileExistsError:
            if resume is None:
                raise WorkflowError(
                    "MODEL_ATTEMPT_UNRESOLVED",
                    "该批次已有模型调用记录且无可复用结果；不会重复计费。",
                ) from None
            invoke = resume
        result = invoke()
        atomic_save(folder / "response.json", result.model_dump_json())
        return result
