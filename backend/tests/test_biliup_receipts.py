import json
import subprocess
from pathlib import Path

import pytest
from conftest import Assets, Clock
from test_publications import manifest

from youmo_workflow.adapters.biliup import BiliupGateway
from youmo_workflow.adapters.biliup_receipts import ReceiptJournal, scrub_text
from youmo_workflow.domain.remote import UploadFiles


def credentials(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cookie_info": {
                    "cookies": [
                        {"name": "SESSDATA", "value": "synthetic-session-secret"},
                        {"name": "bili_jct", "value": "synthetic-csrf-secret"},
                    ]
                },
                "token_info": {"access_token": "synthetic-access-secret"},
            }
        ),
        encoding="utf-8",
    )


def test_redaction_does_not_remove_author_or_review_status_fields() -> None:
    text = '{"author":"public-name","author_status":1,"status":3,"reject_comment":"reason"}'
    assert scrub_text(text, set()) == text


def test_receipts_redact_credentials_and_keep_failure_diagnostics(
    tmp_path: Path, assets: Assets, clock: Clock
) -> None:
    private = tmp_path / "private-session.json"
    credentials(private)
    journal = ReceiptJournal(tmp_path / "receipts", private, manifest(assets, clock))
    output = f'Error: network timeout\n{private}\nsynthetic-session-secret\naccess_token=synthetic-access-secret\nhttps://upload.example/?auth_key=ephemeral-value\nv_voucher: String("voucher_synthetic-one-time-challenge")\nBV1234567890'
    journal.record("upload", subprocess.CompletedProcess([], 1, output, "synthetic-csrf-secret"))
    assert journal.directory
    saved = (journal.directory / "upload.json").read_text(encoding="utf-8")
    for secret in (
        "synthetic-session-secret",
        "synthetic-access-secret",
        "synthetic-csrf-secret",
        "ephemeral-value",
        "private-session.json",
        "voucher_synthetic-one-time-challenge",
    ):
        assert secret not in saved
    assert "network timeout" in saved and "BV1234567890" in saved
    journal.failure(
        "upload",
        subprocess.TimeoutExpired(
            "ignored private command", 1, output=b"BV1234567890", stderr=b"synthetic-session-secret"
        ),
    )
    assert "TimeoutExpired" in (journal.directory / "upload-failure.json").read_text()
    assert "BV1234567890" in (journal.directory / "upload.json").read_text()


def test_gateway_archives_receipt_before_returning_bvid(
    tmp_path: Path, assets: Assets, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, private = tmp_path / "synthetic.exe", tmp_path / "private.json"
    executable.write_bytes(b"not executed")
    credentials(private)

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "created BV1234567890", "")

    monkeypatch.setattr(subprocess, "run", run)
    value = manifest(assets, clock).model_copy(
        update={"test_only": False, "rights_confirmed": True, "visibility": "public"}
    )
    gateway = BiliupGateway(executable, private, tmp_path / "receipts")
    assert (
        gateway.submit(
            value,
            UploadFiles(
                media=tmp_path / "synthetic.mp4", cover=tmp_path / "synthetic.jpg", subtitle=None
            ),
        )
        == "BV1234567890"
    )
    journals = list((tmp_path / "receipts").rglob("upload.json"))
    assert len(journals) == 1 and json.loads(journals[0].read_text())["return_code"] == 0
