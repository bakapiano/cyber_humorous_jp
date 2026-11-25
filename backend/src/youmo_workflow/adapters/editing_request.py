from collections.abc import Callable

from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError

JSON_DIRECTIVE = (
    "\n\n输出协议：请返回一个合法的 JSON 对象，结构严格遵循上述要求。不要输出Markdown围栏或解释。"
)


def range_request(
    content_id: str, prompt: str, cached: Callable[[GenerationRequest], str | None] | None
) -> GenerationRequest:
    original = GenerationRequest(content_id=content_id, stage="editing_v003", prompt=prompt)
    if cached:
        try:
            if cached(original) is not None:
                return original
        except WorkflowError as error:
            if error.code == "MODEL_OUTPUT_INVALID":
                return original
            raise
    # A protocol-only version: the pinned research prompt and original evidence
    # stay unchanged. Older HTTP failures and any paid responses remain archived.
    return original.model_copy(
        update={"stage": "editing_v003_json", "prompt": prompt + JSON_DIRECTIVE}
    )
