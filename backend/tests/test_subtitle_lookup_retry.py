from pathlib import Path

import httpx
import pytest
from test_remote import FakeGateway

from youmo_workflow.adapters.bilibili_subtitles import SubtitleGateway
from youmo_workflow.adapters.biliup import BiliupGateway
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.remote import RemoteSubmission


@pytest.mark.parametrize("status,retryable", [(0, True), (429, True), (503, True), (403, False)])
def test_only_transient_read_failures_use_remaining_finalizer_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int, retryable: bool
) -> None:
    def failed_read(_self: SubtitleGateway, _submission: RemoteSubmission, _subtitle: Path) -> bool:
        if status == 0:
            raise httpx.ConnectError("synthetic network failure")
        request = httpx.Request("GET", "https://example.invalid/read-only")
        response = httpx.Response(status, request=request)
        raise httpx.HTTPStatusError("synthetic HTTP failure", request=request, response=response)

    monkeypatch.setattr(SubtitleGateway, "verify_subtitles", failed_read)
    gateway = BiliupGateway(tmp_path / "unused.exe", tmp_path / "unused.json")
    with pytest.raises(WorkflowError) as error:
        gateway.verify_subtitles(FakeGateway().inspect("BV1234567890"), tmp_path / "unused.srt")
    assert error.value.code == "SUBTITLE_VERIFY_FAILED"
    assert error.value.retryable is retryable
