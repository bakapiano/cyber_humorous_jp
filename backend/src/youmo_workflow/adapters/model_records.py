import hashlib
import json
import re
from pathlib import Path

import httpx
from pydantic import BaseModel, JsonValue, ValidationError

from youmo_workflow.adapters.model_route import ModelRoute
from youmo_workflow.adapters.production_files import encode, freeze, save, save_bytes
from youmo_workflow.adapters.selection_models import CompletionResponse, SavedCompletion
from youmo_workflow.domain.errors import WorkflowError


def request_record(
    root: Path | None, hashes: tuple[str, str], payload: dict[str, JsonValue]
) -> Path | None:
    if root is None:
        return None
    if not all(re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes):
        raise WorkflowError("MODEL_ARCHIVE_ID_INVALID", "模型留档指纹无效。")
    directory = root / hashes[0] / hashes[1]
    freeze(directory / "payload.json", encode(payload))
    return directory


def response_record(directory: Path | None, body: str, key: str) -> None:
    if private_response(body, key):
        raise WorkflowError("MODEL_RESPONSE_PRIVATE", "响应包含私有信息，未保存或返回。")
    if directory is not None:
        save(directory / "provider-response.json", body)


def private_response(body: str, key: str) -> bool:
    if key in body:
        return True
    try:
        decoded: object = json.loads(body)
    except ValueError:
        return False
    return key in json.dumps(decoded, ensure_ascii=False)


class HttpReceipt(BaseModel):
    status_code: int
    body_bytes: int
    body_sha256: str


def http_record(directory: Path | None, response: httpx.Response, key: str) -> None:
    if private_response(response.text, key):
        raise WorkflowError("MODEL_RESPONSE_PRIVATE", "响应包含私有信息，未保存或返回。")
    if directory is not None:
        receipt = HttpReceipt(
            status_code=response.status_code,
            body_bytes=len(response.content),
            body_sha256=hashlib.sha256(response.content).hexdigest(),
        )
        save(directory / "provider-http.json", receipt.model_dump_json())
    if len(response.content) > 4000000:
        raise WorkflowError("MODEL_RESPONSE_TOO_LARGE", "模型响应超过保护上限，已留存状态与哈希。")
    if directory is not None:
        save_bytes(directory / "provider-response.json", response.content)
    if response.status_code != 200:
        raise WorkflowError(
            "MODEL_HTTP_ERROR", f"模型返回 HTTP {response.status_code}；原响应已留档，不自动重发。"
        )


def completion_record(
    body: str, fingerprint: str, route: ModelRoute = ModelRoute.DIRECT
) -> SavedCompletion:
    try:
        result = CompletionResponse.model_validate_json(body)
    except ValidationError:
        raise WorkflowError("MODEL_RESPONSE_INVALID", "模型响应协议无效；原响应已留档。") from None
    if len(result.choices) != 1:
        raise WorkflowError("MODEL_RESPONSE_INVALID", "模型未返回唯一结果。")
    choice = result.choices[0]
    return SavedCompletion(
        request_sha256=fingerprint,
        model=route.receipt_model(result.model),
        content=choice.message.content,
        finish_reason=choice.finish_reason,
        usage=result.usage,
    )


def recover_completion(directory: Path, fingerprint: str) -> SavedCompletion | None:
    raw = directory / "provider-response.json"
    if not raw.exists():
        return None
    http = directory / "provider-http.json"
    if http.exists():
        receipt = HttpReceipt.model_validate_json(http.read_bytes())
        if (
            receipt.status_code != 200
            or hashlib.sha256(raw.read_bytes()).hexdigest() != receipt.body_sha256
        ):
            raise WorkflowError(
                "MODEL_ARCHIVE_NOT_REUSABLE", "归档响应状态或哈希无效，不追加请求。"
            )
    route = (
        ModelRoute.WORKBUDDY if (directory / "wire-request.json").is_file() else ModelRoute.DIRECT
    )
    return completion_record(raw.read_text(encoding="utf-8"), fingerprint, route)
