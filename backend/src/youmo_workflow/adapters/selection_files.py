import hashlib
from pathlib import Path

from pydantic import BaseModel, ValidationError

from youmo_workflow.adapters.selection_models import FileManifest
from youmo_workflow.domain.errors import WorkflowError


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_model[T: BaseModel](path: Path, model: type[T]) -> T:
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        raise WorkflowError(
            "SELECTION_CONFIG_INVALID", "初筛配置或冻结资料缺失／格式无效。"
        ) from None


def read_lines[T: BaseModel](path: Path, model: type[T]) -> list[T]:
    try:
        return [
            model.model_validate_json(line)
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    except (OSError, ValidationError):
        raise WorkflowError("SELECTION_HISTORY_INVALID", "历史原标题资料缺失或格式无效。") from None


def durable_path(value: str, root: Path) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(root.resolve()) or ".tmp" in path.parts:
        raise WorkflowError(
            "SELECTION_PATH_INVALID", "初筛正式输入必须位于指定来源模块且不依赖临时目录。"
        )
    return path


def checked_bytes(path: Path, folder: Path) -> bytes:
    manifest = read_model(folder / "file_manifest.json", FileManifest)
    relative = path.relative_to(folder).as_posix()
    expected = manifest.hashes_sha256.get(relative)
    if expected is None:
        expected = next((row.sha256 for row in manifest.files if row.path == relative), None)
    try:
        raw = path.read_bytes()
    except OSError:
        raise WorkflowError("SELECTION_INPUT_MISSING", "初筛冻结输入缺失。") from None
    if expected is None or sha256(raw) != expected:
        raise WorkflowError("SELECTION_INPUT_CHANGED", "初筛冻结输入哈希不匹配，未调用模型。")
    return raw


def evidence_matches(quote: str, title: str) -> bool:
    def normalize(value: str) -> str:
        return " ".join(value.translate(str.maketrans({"｢": "「", "｣": "」"})).split())

    return bool(quote.strip()) and (quote in title or normalize(quote) in normalize(title))
