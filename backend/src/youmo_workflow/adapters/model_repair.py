import hashlib
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue, ValidationError

from youmo_workflow.adapters.model_attempts import RepairTransport, correction_attempt
from youmo_workflow.adapters.production_files import encode, freeze, save
from youmo_workflow.adapters.selection_models import SavedCompletion
from youmo_workflow.domain.errors import WorkflowError

MAX_CORRECTIONS = 2
FORMAT_ERRORS = {
    "PRODUCTION_VALIDATION_FAILED",
    "TRANSLATION_SEGMENTS_CHANGED",
    "TRANSLATION_MASK_CHANGED",
    "INITIAL_SELECTION_INVALID",
    "INITIAL_SELECTION_EVIDENCE_INVALID",
    "INITIAL_SELECTION_INCOMPLETE",
}


def validation_feedback(
    result: SavedCompletion,
    validate: Callable[[SavedCompletion], object],
    expected_model: JsonValue,
) -> str | None:
    if result.model != expected_model or result.finish_reason not in {"stop", "length"}:
        raise WorkflowError("MODEL_OUTPUT_INVALID", "模型版本或停止原因不允许格式纠正。")
    if result.finish_reason == "length":
        return (
            "模型输出被截断（finish_reason=length）。请返回符合原契约的完整JSON，不添加长篇解释。"
        )
    if not result.content:
        return "模型返回了空内容。必须按原契约返回完整JSON。"
    try:
        validate(result)
    except ValidationError as error:
        return error.json(include_input=False, include_url=False)
    except WorkflowError as error:
        if error.code not in FORMAT_ERRORS:
            raise
        return f"{error.code}: {error.message}"
    return None


class FormatRepair:
    def __init__(
        self, directory: Path, payload: dict[str, JsonValue], transport: RepairTransport
    ) -> None:
        self.directory, self.payload, self.transport = directory, payload, transport

    def run(
        self,
        initial: SavedCompletion,
        validate: Callable[[SavedCompletion], object],
        *,
        allow_corrections: bool = True,
    ) -> SavedCompletion:
        messages = self.payload.get("messages")
        if not isinstance(messages, list):
            raise WorkflowError("MODEL_INPUT_INVALID", "模型上下文缺失，未追加请求。")
        history = messages[:]
        current = initial
        max_corrections = MAX_CORRECTIONS if allow_corrections else 0
        for index in range(max_corrections + 1):
            error = validation_feedback(current, validate, self.payload.get("model"))
            record = encode(
                {
                    "attempt": index,
                    "max_corrections": max_corrections,
                    "response_sha256": hashlib.sha256(
                        current.model_dump_json().encode()
                    ).hexdigest(),
                    "error": error,
                    "valid": error is None,
                }
            )
            freeze(self.directory / f"validation-{index:02}.json", record)
            if error is None:
                save(self.directory / "accepted.json", record)
                return current
            if not allow_corrections:
                raise WorkflowError(
                    "MODEL_FALLBACK_INVALID", "备用模型结果未通过校验，不追加请求。"
                )
            if index == max_corrections:
                raise WorkflowError(
                    "MODEL_FORMAT_RETRIES_EXHAUSTED",
                    "初次输出及两次纠正仍未通过校验；已保存全部上下文与错误，不再追加请求。",
                )
            history.extend(
                [
                    {"role": "assistant", "content": current.content or ""},
                    {"role": "user", "content": correction_prompt(error)},
                ]
            )
            payload = {**self.payload, "messages": history[:]}
            current = correction_attempt(
                self.directory / f"corrections/{index + 1:02}", payload, self.transport
            )
        raise AssertionError("Bounded correction loop must return or fail")


def correction_prompt(error: str) -> str:
    return (
        "上一轮输出未通过程序校验，错误如下：\n"
        + error
        + "\n请结合前面的原始输入和已有输出，仅纠正格式、字段及契约约束。保留所有受原输入支持的信息，"
        "不得补造事实、证据或候选ID，不得修改已固定的标题或原始素材；星号遮罩数量必须保留。"
        "原始输入仍是唯一依据。返回符合原契约的完整JSON，不要只输出补丁，不要添加Markdown或解释。"
    )
