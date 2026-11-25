import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from youmo_workflow.adapters.model_http import recover_before_retry
from youmo_workflow.adapters.model_records import private_response
from youmo_workflow.adapters.production_files import encode, freeze, save
from youmo_workflow.adapters.selection_models import SavedCompletion
from youmo_workflow.domain.errors import WorkflowError


@dataclass
class RepairTransport:
    credential: Callable[[], str]
    invoke: Callable[[dict[str, JsonValue], str, str, Path], SavedCompletion]
    resume: Callable[[dict[str, JsonValue], str, str, Path], SavedCompletion] | None = None


def correction_attempt(
    directory: Path, payload: dict[str, JsonValue], transport: RepairTransport
) -> SavedCompletion:
    serialized = encode(payload)
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    path = directory / "response.json"
    if path.exists():
        saved = SavedCompletion.model_validate_json(path.read_bytes())
    else:
        saved = recover_before_retry(directory, fingerprint, transport.resume is not None)
    if saved is not None:
        if (
            saved.request_sha256 != fingerprint
            or (directory / "payload.json").read_text(encoding="utf-8") != serialized
        ):
            raise WorkflowError("PRODUCTION_INPUT_CHANGED", "纠正响应与完整上下文不匹配。")
        return saved
    key = transport.credential()
    if private_response(serialized, key):
        raise WorkflowError("PRIVATE_MODEL_INPUT", "纠正上下文含私有信息，未保存或调用。")
    freeze(directory / "payload.json", serialized)
    freeze(directory / "fingerprint.txt", fingerprint)
    invoke = transport.invoke
    try:
        with (directory / "attempt.json").open("x", encoding="utf-8") as stream:
            stream.write(encode({"max_calls": 1, "request_sha256": fingerprint}))
    except FileExistsError:
        if transport.resume is None:
            raise WorkflowError(
                "MODEL_ATTEMPT_UNRESOLVED", "该次纠正已有调用记录且无可复用响应，不重发。"
            ) from None
        invoke = transport.resume
    saved = invoke(payload, key, fingerprint, directory)
    if saved.request_sha256 != fingerprint:
        raise WorkflowError("PRODUCTION_INPUT_CHANGED", "纠正响应指纹不匹配。")
    save(path, saved.model_dump_json())
    return saved
