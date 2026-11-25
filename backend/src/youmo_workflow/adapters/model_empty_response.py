"""Recognize an empty transport response, not a model refusal or invalid model JSON."""

import hashlib
from pathlib import Path

from youmo_workflow.adapters.model_records import HttpReceipt
from youmo_workflow.adapters.production_files import encode, freeze


def empty_protocol_response(directory: Path) -> bool:
    raw, receipt = directory / "provider-response.json", directory / "provider-http.json"
    if not raw.exists() or not receipt.exists():
        return False
    saved = HttpReceipt.model_validate_json(receipt.read_bytes())
    body = raw.read_bytes()
    return (
        saved.status_code == 200
        and len(body) <= 64
        and hashlib.sha256(body).hexdigest() == saved.body_sha256
        and body.strip() in {b"", b"null"}
    )


def note_empty_protocol(directory: Path) -> None:
    freeze(
        directory / "empty-protocol-recovery.json",
        encode(
            {
                "reason": "HTTP200 contains only null or whitespace, no completion to validate",
                "original_response_sha256": hashlib.sha256(
                    (directory / "provider-response.json").read_bytes()
                ).hexdigest(),
                "original_records_preserved": True,
                "uses_existing_network_retry_budget": True,
            }
        ),
    )
