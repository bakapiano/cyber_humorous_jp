"""Materialize exact historic model inputs only when their recorded hash matches."""

import hashlib
import json
from pathlib import Path

from youmo_workflow.adapters.production_files import encode, freeze
from youmo_workflow.adapters.production_model import model_payload
from youmo_workflow.config import get_settings
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError


def archive_payload(request_path: Path) -> bool:
    directory = request_path.parent
    fingerprint_path = directory / "fingerprint.txt"
    if not fingerprint_path.is_file():
        return False
    request = GenerationRequest.model_validate_json(request_path.read_bytes())
    serialized = encode(model_payload(request))
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    if fingerprint_path.read_text(encoding="utf-8").strip() != fingerprint:
        raise WorkflowError(
            "MODEL_PAYLOAD_UNVERIFIED", "历史模型参数与请求指纹不匹配，未伪造或覆盖输入留档。"
        )
    existed = (directory / "payload.json").exists()
    freeze(directory / "payload.json", serialized)
    if not existed:
        freeze(
            directory / "payload-provenance.json",
            encode(
                {
                    "mode": "verified_reconstruction",
                    "sha256": fingerprint,
                    "provider_envelope_available": (directory / "provider-response.json").is_file(),
                    "answer_body_retained": (directory / "response.json").is_file(),
                }
            ),
        )
    return not existed


def archive_all(root: Path) -> dict[str, int]:
    scanned = created = 0
    for path in root.glob("*/*/request.json"):
        if not path.resolve().is_relative_to(root.resolve()):
            raise WorkflowError("MODEL_ARCHIVE_PATH_DENIED", "模型留档目录越界。")
        scanned += 1
        created += archive_payload(path)
    return {"requests_checked": scanned, "payloads_created": created, "model_calls": 0}


def main() -> None:
    try:
        print(json.dumps(archive_all(get_settings().root / "data/production")))
    except WorkflowError as error:
        print(json.dumps({"error": error.code, "message": error.message}, ensure_ascii=True))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
