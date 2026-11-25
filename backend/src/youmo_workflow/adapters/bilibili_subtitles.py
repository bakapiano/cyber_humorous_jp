import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pysubs2
from pydantic import JsonValue, ValidationError

from youmo_workflow.adapters.bilibili_models import (
    Credentials,
    Cue,
    PlayerResponse,
    ReviewLanguages,
    ReviewResponse,
    SubtitleBody,
    Track,
    WriteResponse,
)
from youmo_workflow.adapters.subtitle_journal import SubtitleJournal
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.remote import RemoteSubmission


def read_credentials(path: Path) -> tuple[str, str]:
    try:
        credentials = Credentials.model_validate_json(path.read_bytes()).cookie_info.cookies
    except (OSError, ValidationError):
        raise WorkflowError("BILI_LOGIN_REQUIRED", "B站凭据不可读或格式无效。") from None
    csrf = next((item.value for item in credentials if item.name == "bili_jct"), "")
    if not csrf:
        raise WorkflowError("BILI_LOGIN_REQUIRED", "B站凭据缺少 CSRF 字段。")
    return "; ".join(f"{item.name}={item.value}" for item in credentials), csrf


def local_cues(path: Path) -> list[Cue]:
    subtitles = pysubs2.load(str(path), encoding="utf-8")
    return [
        Cue.model_validate(
            {"from": event.start / 1000, "to": event.end / 1000, "content": event.plaintext}
        )
        for event in subtitles.events
    ]


class SubtitleGateway:
    def __init__(
        self, credentials: Path, client: httpx.Client, receipt_root: Path | None = None
    ) -> None:
        self.credentials, self.client = credentials, client
        self.receipt_root = receipt_root

    def headers(self) -> dict[str, str]:
        cookie, _ = read_credentials(self.credentials)
        return {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://member.bilibili.com/",
        }

    def tracks(self, bvid: str, cid: int) -> list[Track]:
        response = self.client.get(
            "https://api.bilibili.com/x/player/wbi/v2",
            params={"bvid": bvid, "cid": cid},
            headers=self.headers(),
        )
        response.raise_for_status()
        payload = PlayerResponse.model_validate_json(response.content)
        if payload.code != 0 or payload.data is None:
            raise WorkflowError("SUBTITLE_LOOKUP_FAILED", "无法核验平台字幕轨。")
        return [track for track in payload.data.subtitle.subtitles if track.lan == "zh"]

    def read_track(self, track: Track) -> list[Cue]:
        url = (
            "https:" + track.subtitle_url
            if track.subtitle_url.startswith("//")
            else track.subtitle_url
        )
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not parsed.hostname.endswith(".hdslb.com")
        ):
            raise WorkflowError("SUBTITLE_CDN_DENIED", "字幕地址不在允许的 CDN 范围。")
        if parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise WorkflowError("SUBTITLE_CDN_DENIED", "字幕地址格式不受信。")
        # The official subtitle editor returns http URLs for review drafts.
        # Upgrade trusted CDN URLs; never send captions or credentials over HTTP.
        url = parsed._replace(scheme="https").geturl()
        response = self.client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        return SubtitleBody.model_validate_json(response.content).body

    def attach_and_verify(
        self, submission: RemoteSubmission, path: Path, *, subtitle_id: str = "0"
    ) -> bool:
        if len(submission.videos) != 1 or submission.archive.is_only_self not in {0, 1}:
            raise WorkflowError("SINGLE_PART_REQUIRED", "仅为已核实可见性的单P稿件挂载字幕。")
        bvid, cid = submission.archive.bvid, submission.videos[0].cid
        expected = local_cues(path)
        tracks = self.tracks(bvid, cid)
        if tracks:
            if len(tracks) == 1 and self.read_track(tracks[0]) == expected:
                return True
            raise WorkflowError("SUBTITLE_CONFLICT", "已有字幕与本次产物不一致，拒绝覆盖。")
        self.save(bvid, cid, expected, subtitle_id=subtitle_id)
        tracks = self.tracks(bvid, cid)
        return len(tracks) == 1 and self.read_track(tracks[0]) == expected

    def verify_subtitles(self, submission: RemoteSubmission, path: Path) -> bool:
        if len(submission.videos) != 1:
            raise WorkflowError("SINGLE_PART_REQUIRED", "当前只支持单P字幕核验。")
        tracks = self.tracks(submission.archive.bvid, submission.videos[0].cid)
        expected = local_cues(path)
        if len(tracks) == 1 and self.read_track(tracks[0]) == expected:
            return True
        self.check_rejection(submission, expected)
        return False

    def check_rejection(self, submission: RemoteSubmission, expected: list[Cue]) -> None:
        result = self.rejected_subtitle(submission, expected)
        if result:
            raise WorkflowError("SUBTITLE_REVIEW_REJECTED", f"字幕审核退回：{result[1][:180]}")

    def rejected_subtitle(
        self, submission: RemoteSubmission, expected: list[Cue]
    ) -> tuple[str, str] | None:
        bvid, cid = submission.archive.bvid, submission.videos[0].cid
        response = self.client.get(
            "https://api.bilibili.com/x/v2/dm/subtitle/lans",
            params={"type": 1, "oid": cid},
            headers=self.headers(),
        )
        response.raise_for_status()
        languages = ReviewLanguages.model_validate_json(response.content)
        if languages.code != 0:
            raise WorkflowError(
                "SUBTITLE_REVIEW_LOOKUP_FAILED", "字幕审核状态查询失败。", retryable=True
            )
        for language in languages.data or []:
            if language.lan != "zh" or language.audit_back is None:
                continue
            response = self.client.get(
                "https://api.bilibili.com/x/v2/dm/subtitle/show",
                params={"oid": cid, "subtitle_id": language.audit_back.subtitle_id_str},
                headers=self.headers(),
            )
            response.raise_for_status()
            detail = ReviewResponse.model_validate_json(response.content)
            row = detail.data
            if (
                detail.code == 0
                and row
                and row.status == 3
                and row.upper_status == 1
                and row.oid == cid
                and row.bvid == bvid
                and row.lan == "zh"
                and self.read_track(Track(lan="zh", subtitle_url=row.subtitle_url)) == expected
            ):
                journal = SubtitleJournal(self.receipt_root, self.credentials, bvid, cid)
                journal.response("review-rejected", response)
                return language.audit_back.subtitle_id_str, journal.safe_text(row.reject_comment)
        return None

    def revise_and_verify(
        self, submission: RemoteSubmission, previous: Path, revised: Path, subtitle_id: str
    ) -> bool:
        rejected = self.rejected_subtitle(submission, local_cues(previous))
        if rejected is None or rejected[0] != subtitle_id:
            raise WorkflowError(
                "SUBTITLE_REVISION_TARGET_CHANGED", "原字幕的退回记录已改变，未提交修订。"
            )
        return self.attach_and_verify(submission, revised, subtitle_id=subtitle_id)

    def save(self, bvid: str, cid: int, cues: list[Cue], *, subtitle_id: str = "0") -> None:
        if not re.fullmatch(r"[0-9]{1,25}", subtitle_id):
            raise WorkflowError("INVALID_SUBTITLE_ID", "字幕版本编号无效。")
        _, csrf = read_credentials(self.credentials)
        body: dict[str, JsonValue] = {
            "font_size": 0.4,
            "font_color": "#FFFFFF",
            "background_alpha": 0.5,
            "background_color": "#000000",
            "stroke": "none",
            "body": [cue.model_dump(by_alias=True) for cue in cues],
        }
        data = {
            "lan": "zh",
            "submit": "true",
            "csrf": csrf,
            "sign": "false",
            "bvid": bvid,
            "type": "1",
            "oid": str(cid),
            "data": json.dumps(body, ensure_ascii=False),
        }
        if subtitle_id != "0":
            data["origin_subtitle_id"] = subtitle_id
        journal = SubtitleJournal(self.receipt_root, self.credentials, bvid, cid)
        journal.begin(body, subtitle_id=subtitle_id)
        try:
            response = self.client.post(
                "https://api.bilibili.com/x/v2/dm/subtitle/draft/save",
                data=data,
                headers={
                    **self.headers(),
                    "Origin": "https://account.bilibili.com",
                    "Referer": f"https://account.bilibili.com/subtitle/edit/#/editor?bvid={bvid}&cid={cid}",
                },
            )
            journal.response("save", response)
            response.raise_for_status()
            code = WriteResponse.model_validate_json(response.content).code
            if code != 0:
                raise WorkflowError("SUBTITLE_SAVE_REJECTED", f"平台拒绝字幕保存，错误码{code}。")
        except httpx.HTTPError as error:
            journal.failure(error)
            raise WorkflowError(
                "SUBTITLE_RESULT_UNKNOWN", "字幕写入结果不明，禁止盲目重复写入。"
            ) from None
