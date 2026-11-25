import re
import subprocess
import time
from pathlib import Path

import httpx
from pydantic import ValidationError

from youmo_workflow.adapters.bilibili_subtitles import SubtitleGateway, local_cues
from youmo_workflow.adapters.biliup_receipts import ReceiptJournal
from youmo_workflow.adapters.biliup_rejections import submit_rate_limited
from youmo_workflow.adapters.media import probe
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.remote import RemoteSubmission, UploadFiles


class BiliupGateway:
    def __init__(
        self, executable: Path, credentials: Path, receipt_root: Path | None = None
    ) -> None:
        self.executable, self.credentials = executable, credentials
        self.receipt_root = receipt_root

    def preflight(self, manifest: PublicationManifest, files: UploadFiles) -> None:
        if not self.executable.is_file() or not self.credentials.is_file():
            raise WorkflowError("BILI_NOT_CONFIGURED", "投稿工具或本地凭据未配置。")
        self.upload_command(manifest, files)

    def private_command(self, manifest: PublicationManifest, files: UploadFiles) -> list[str]:
        if manifest.visibility != "private":
            raise WorkflowError("PRIVATE_UPLOAD_NOT_ELIGIBLE", "私密投稿接口不能发布公开稿件。")
        return self.upload_command(manifest, files)

    def upload_command(self, manifest: PublicationManifest, files: UploadFiles) -> list[str]:
        if manifest.test_only or not manifest.rights_confirmed:
            raise WorkflowError(
                "PRIVATE_UPLOAD_NOT_ELIGIBLE",
                "真实上传须为已确认使用权限及可见性的非测试转载稿件。",
            )
        return [
            str(self.executable),
            "-u",
            str(self.credentials),
            "--rust-log",
            "warn,biliup::uploader::bilibili=info",
            "upload",
            str(files.media),
            "--submit",
            "web",
            "--limit",
            "1",
            "--copyright",
            "2",
            "--source",
            manifest.source_url,
            "--is-only-self",
            str(int(manifest.visibility == "private")),
            "--tid",
            "122",
            "--title",
            manifest.title,
            "--desc",
            f"转载来源：{manifest.source_url}\nAI辅助翻译与剪辑；机器字幕可能存在识别误差。",
            "--tag",
            "翻译",
            "--cover",
            str(files.cover),
        ]

    def submit(self, manifest: PublicationManifest, files: UploadFiles) -> str:
        if not self.executable.is_file() or not self.credentials.is_file():
            raise WorkflowError("BILI_NOT_CONFIGURED", "投稿工具或本地凭据未配置。")
        command = self.upload_command(manifest, files)
        journal = ReceiptJournal(self.receipt_root, self.credentials, manifest)
        started = time.time()
        try:
            result = subprocess.run(
                command,
                cwd=self.executable.parent,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            journal.failure("upload", error)
            raise WorkflowError("SUBMIT_RESULT_UNKNOWN", "上传结果不明，先对账，不重发。") from None
        journal.record("upload", result)
        matches = set(re.findall(r"BV[A-Za-z0-9]{10}", result.stdout + result.stderr))
        if submit_rate_limited(result.returncode, result.stdout, result.stderr):
            raise WorkflowError(
                "BILI_SUBMIT_RATE_LIMIT",
                "B站137022明确拒绝本次投稿，进入账号级退避冷却。",
                retryable=True,
            )
        if result.returncode and not matches and preupload_rate_limited(result.stderr):
            raise WorkflowError(
                "BILI_PREUPLOAD_RATE_LIMIT",
                "B站601预上传限流，尚未提交稿件；进入账号级冷却。",
                retryable=True,
            )
        if result.returncode or len(matches) != 1:
            return self.locate_receipt(manifest, files, started, journal)
        return matches.pop()

    def locate_receipt(
        self,
        manifest: PublicationManifest,
        files: UploadFiles,
        started: float,
        journal: ReceiptJournal | None = None,
    ) -> str:
        try:
            listing = subprocess.run(
                [str(self.executable), "-u", str(self.credentials), "list", "--max-pages", "2"],
                cwd=self.executable.parent,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            if journal:
                journal.failure("lookup", error)
            raise WorkflowError(
                "SUBMIT_RESULT_UNKNOWN", "回执缺失且稿件列表不可查，禁止重传。"
            ) from None
        if journal:
            journal.record("lookup", listing)
        candidates = set(re.findall(r"BV[A-Za-z0-9]{10}", listing.stdout))
        if listing.returncode or len(candidates) > 60:
            raise WorkflowError("SUBMIT_RESULT_UNKNOWN", "稿件列表无法安全对账。")
        duration = probe(files.media).duration_seconds
        matches = [
            bvid
            for bvid in candidates
            if receipt_matches(self.inspect(bvid), manifest, started, duration)
        ]
        if len(matches) != 1:
            raise WorkflowError(
                "SUBMIT_RESULT_UNKNOWN", "无法确认唯一匹配可见性的转载稿件，未重传。"
            )
        return matches[0]

    def inspect(self, bvid: str) -> RemoteSubmission:
        if not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
            raise WorkflowError("INVALID_REMOTE_ID", "稿件编号无效。")
        try:
            return self.inspect_validated(bvid)
        except (OSError, subprocess.TimeoutExpired, ValidationError):
            raise WorkflowError("REMOTE_LOOKUP_FAILED", "稿件查询失败或返回格式无效。") from None

    def inspect_validated(self, bvid: str) -> RemoteSubmission:
        result = subprocess.run(
            [str(self.executable), "-u", str(self.credentials), "show", bvid],
            cwd=self.executable.parent,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise WorkflowError("REMOTE_LOOKUP_FAILED", "稿件查询失败。")
        submission = RemoteSubmission.model_validate_json(result.stdout)
        if submission.archive.bvid != bvid:
            raise WorkflowError("REMOTE_ID_MISMATCH", "返回稿件身份不符。")
        return submission

    def attach_and_verify(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
            return SubtitleGateway(self.credentials, client, self.receipt_root).attach_and_verify(
                submission, subtitle
            )

    def rejected_subtitle(
        self, submission: RemoteSubmission, previous: Path
    ) -> tuple[str, str] | None:
        with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
            return SubtitleGateway(self.credentials, client, self.receipt_root).rejected_subtitle(
                submission, local_cues(previous)
            )

    def revise_subtitles(
        self, submission: RemoteSubmission, previous: Path, revised: Path, subtitle_id: str
    ) -> bool:
        with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
            return SubtitleGateway(self.credentials, client, self.receipt_root).revise_and_verify(
                submission, previous, revised, subtitle_id
            )

    def verify_subtitles(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        try:
            with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
                return SubtitleGateway(
                    self.credentials, client, self.receipt_root
                ).verify_subtitles(submission, subtitle)
        except httpx.HTTPError as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else 0
            raise WorkflowError(
                "SUBTITLE_VERIFY_FAILED",
                "字幕回查失败，未执行远端写入。",
                retryable=isinstance(error, httpx.TransportError)
                or status in {408, 425, 429}
                or status >= 500,
            ) from None
        except (ValidationError, OSError):
            raise WorkflowError(
                "SUBTITLE_VERIFY_FAILED", "字幕回查失败，未执行远端写入。"
            ) from None


def receipt_matches(
    submission: RemoteSubmission, manifest: PublicationManifest, started: float, duration: float
) -> bool:
    archive = submission.archive
    return (
        archive.title == manifest.title
        and archive.source == manifest.source_url
        and archive.copyright == 2
        and archive.is_only_self == int(manifest.visibility == "private")
        and started - 5 <= archive.ctime <= time.time() + 300
        and len(submission.videos) == 1
        and abs(submission.videos[0].duration - duration) <= 1
    )


def preupload_rate_limited(stderr: str) -> bool:
    # Pinned biliup 1.3.4: this error exits the pre_upload retry loop before
    # media upload and before studio.submit. Do not generalize to other errors.
    return (
        "upload rate limit (code: 601)" in stderr
        and "after retries" in stderr
        and "crates/biliup-cli/src/uploader.rs:579:" in stderr.replace("\\", "/")
    )
