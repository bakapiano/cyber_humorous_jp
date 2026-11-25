import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, JsonValue

from youmo_workflow.domain.errors import WorkflowError


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encode(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid4().hex}.new")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def save_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid4().hex}.new")
    temporary.write_bytes(body)
    temporary.replace(path)


def freeze(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != text:
            raise WorkflowError(
                "PRODUCTION_INPUT_CHANGED", "该阶段已有不同的冻结输入，未覆写或追加模型请求。"
            ) from None


def folder(root: Path, content_id: str, stage: str) -> Path:
    if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", part) for part in (content_id, stage)):
        raise WorkflowError("PRODUCTION_ID_INVALID", "生产任务标识无效。")
    result = (root / "production" / content_id / stage).resolve()
    if not result.is_relative_to(root.resolve()):
        raise WorkflowError("PRODUCTION_PATH_DENIED", "生产产物目录越界。")
    result.mkdir(parents=True, exist_ok=True)
    return result


class FrozenFile(BaseModel):
    path: str
    sha256: str


class FrozenFiles(BaseModel):
    files: list[FrozenFile]


def frozen_text(path: Path) -> str:
    manifest_path = path.parent / "file_manifest.json"
    if not manifest_path.is_file():
        raise WorkflowError("PROFILE_NOT_FROZEN", "引用的研究版本未冻结。")
    manifest = FrozenFiles.model_validate_json(manifest_path.read_bytes())
    expected = next((row.sha256 for row in manifest.files if row.path == path.name), None)
    if expected != digest(path):
        raise WorkflowError("PROFILE_CHANGED", "研究版本哈希不匹配，未调用模型。")
    return path.read_text(encoding="utf-8")
