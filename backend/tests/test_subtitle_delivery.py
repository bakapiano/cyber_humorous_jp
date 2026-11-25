import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from test_biliup_receipts import credentials
from test_remote import FakeGateway

from youmo_workflow.adapters.bilibili_models import Track
from youmo_workflow.adapters.bilibili_subtitles import SubtitleGateway, local_cues
from youmo_workflow.domain.errors import WorkflowError


def subtitle(path: Path) -> Path:
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\n中文\n日本語\n", encoding="utf-8")
    return path


def test_http_review_url_is_upgraded_without_credentials(tmp_path: Path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(200, json={"body": []})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = SubtitleGateway(tmp_path / "unused.json", client)
        assert (
            gateway.read_track(
                Track(lan="zh", subtitle_url="http://i0.hdslb.com/bfs/subtitle/test.json")
            )
            == []
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/x",
        "https://i0.hdslb.com.evil.example/x",
        "https://user:pass@i0.hdslb.com/x",
        "https://i0.hdslb.com:444/x",
        "ftp://i0.hdslb.com/x",
    ],
)
def test_untrusted_subtitle_url_is_not_requested(tmp_path: Path, url: str) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Untrusted CDN request must not be sent")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(WorkflowError):
            SubtitleGateway(tmp_path / "unused.json", client).read_track(
                Track(lan="zh", subtitle_url=url)
            )


@pytest.mark.parametrize("same_content", [True, False])
def test_review_rejection_is_read_only_and_bound_to_exact_caption(
    tmp_path: Path, same_content: bool
) -> None:
    private, path = tmp_path / "private.json", subtitle(tmp_path / "text.srt")
    credentials(private)
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        calls.append(request.url.path)
        if "/player/" in request.url.path:
            return httpx.Response(200, json={"code": 0, "data": {"subtitle": {"subtitles": []}}})
        if request.url.path.endswith("/lans"):
            return httpx.Response(
                200,
                json={"code": 0, "data": [{"lan": "zh", "audit_back": {"subtitle_id_str": "123"}}]},
            )
        if request.url.path.endswith("/show"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "status": 3,
                        "oid": 1,
                        "bvid": "BV1234567890",
                        "lan": "zh",
                        "upper_status": 1,
                        "subtitle_url": "http://i0.hdslb.com/bfs/subtitle/synthetic.json",
                        "reject_comment": "synthetic rejection",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "body": [
                    {
                        "from": 0,
                        "to": 1,
                        "content": "中文\n日本語" if same_content else "another user's text",
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = SubtitleGateway(private, client, tmp_path / "receipts")
        if same_content:
            with pytest.raises(WorkflowError) as error:
                gateway.verify_subtitles(FakeGateway().inspect("BV1234567890"), path)
            assert error.value.code == "SUBTITLE_REVIEW_REJECTED" and not error.value.retryable
            assert list((tmp_path / "receipts").rglob("review-rejected.json"))
        else:
            assert not gateway.verify_subtitles(FakeGateway().inspect("BV1234567890"), path)
    assert len(calls) == 4


@pytest.mark.parametrize("code", [0, 79008])
def test_save_response_is_archived_without_credentials(tmp_path: Path, code: int) -> None:
    private, path = tmp_path / "private.json", subtitle(tmp_path / "text.srt")
    credentials(private)

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path.endswith("/draft/save")
        fields = parse_qs(request.content.decode())
        assert "subtitle_id" not in fields and "origin_subtitle_id" not in fields
        body = json.loads(fields["data"][0])
        assert body["stroke"] == "none" and "Stroke" not in body
        return httpx.Response(
            200,
            json={
                "code": code,
                "message": "synthetic-session-secret",
                "data": {"v_voucher": "voucher_synthetic-challenge"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = SubtitleGateway(private, client, tmp_path / "receipts")
        if code:
            with pytest.raises(WorkflowError) as error:
                gateway.save("BV1234567890", 1, local_cues(path))
            assert error.value.code == "SUBTITLE_SAVE_REJECTED"
        else:
            gateway.save("BV1234567890", 1, local_cues(path))
    receipts = list((tmp_path / "receipts").rglob("save.json"))
    assert len(receipts) == 1
    text = receipts[0].read_text(encoding="utf-8")
    assert "synthetic-session-secret" not in text and "voucher_synthetic-challenge" not in text
    assert json.loads(text)["http_status"] == 200
    assert receipts[0].with_name("intent.json").exists()


@pytest.mark.parametrize("requested_id", ["123", "999"])
def test_revision_writes_only_the_exact_rejected_id(tmp_path: Path, requested_id: str) -> None:
    private, previous = tmp_path / "private.json", subtitle(tmp_path / "old.srt")
    revised = tmp_path / "new.srt"
    revised.write_text(previous.read_text(encoding="utf-8").replace("中文", "**"), encoding="utf-8")
    credentials(private)
    writes: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            fields = parse_qs(request.content.decode())
            assert "subtitle_id" not in fields
            identifier = fields["origin_subtitle_id"][0]
            assert identifier == "123"
            writes.append(identifier)
            return httpx.Response(200, json={"code": 0})
        if path.endswith("/lans"):
            return httpx.Response(
                200,
                json={"code": 0, "data": [{"lan": "zh", "audit_back": {"subtitle_id_str": "123"}}]},
            )
        if path.endswith("/show"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "status": 3,
                        "oid": 1,
                        "bvid": "BV1234567890",
                        "lan": "zh",
                        "upper_status": 1,
                        "subtitle_url": "https://i0.hdslb.com/old.json",
                        "reject_comment": "synthetic",
                    },
                },
            )
        if "/player/" in path:
            tracks = (
                [{"lan": "zh", "subtitle_url": "https://i0.hdslb.com/new.json"}] if writes else []
            )
            return httpx.Response(
                200, json={"code": 0, "data": {"subtitle": {"subtitles": tracks}}}
            )
        return httpx.Response(
            200,
            json={
                "body": [
                    {
                        "from": 0,
                        "to": 1,
                        "content": "**\n日本語" if path.endswith("new.json") else "中文\n日本語",
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = SubtitleGateway(private, client, tmp_path / "receipts")
        if requested_id == "123":
            assert gateway.revise_and_verify(
                FakeGateway().inspect("BV1234567890"), previous, revised, requested_id
            )
            assert writes == ["123"]
        else:
            with pytest.raises(WorkflowError, match="退回记录已改变"):
                gateway.revise_and_verify(
                    FakeGateway().inspect("BV1234567890"), previous, revised, requested_id
                )
            assert writes == []
