import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError

from youmo_workflow.adapters.bilibili_subtitles import read_credentials
from youmo_workflow.domain.errors import WorkflowError


class BrowserCookie(BaseModel):
    name: str
    value: str = Field(repr=False)
    domain: str
    expirationDate: float | None = None
    httpOnly: bool = True
    secure: bool = True


class Account(BaseModel):
    mid: int
    uname: str
    isLogin: Literal[True]


class AnonymousAccount(BaseModel):
    isLogin: Literal[False]


class AccountResponse(BaseModel):
    code: int
    data: Account | AnonymousAccount | None = None


def load_export(path: Path) -> list[BrowserCookie]:
    try:
        rows = TypeAdapter(list[BrowserCookie]).validate_json(path.read_bytes())
    except (OSError, ValidationError):
        raise WorkflowError(
            "BILI_COOKIE_INVALID", "Cookie 文件不可读或不是支持的浏览器 JSON 导出格式。"
        ) from None
    selected: dict[str, BrowserCookie] = {}
    for row in rows:
        domain = row.domain.lstrip(".")
        if domain != "bilibili.com" and not domain.endswith(".bilibili.com"):
            continue
        if row.expirationDate is not None and row.expirationDate <= time.time():
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", row.name) or re.search(r"[\r\n;]", row.value):
            raise WorkflowError("BILI_COOKIE_INVALID", "Cookie 字段格式不安全。")
        old = selected.get(row.name)
        if old and old.value != row.value:
            raise WorkflowError(
                "BILI_COOKIE_AMBIGUOUS", "导出存在同名不同值的 Cookie，请提供单一账号会话。"
            )
        selected[row.name] = row
    if any(
        not selected.get(key) or not selected[key].value
        for key in ("SESSDATA", "bili_jct", "DedeUserID")
    ):
        raise WorkflowError("BILI_COOKIE_INVALID", "Cookie 缺少有效的登录或 CSRF 字段。")
    return list(selected.values())


def account(client: httpx.Client, header: str) -> Account:
    response = client.get(
        "https://api.bilibili.com/x/web-interface/nav",
        headers={
            "Cookie": header,
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.bilibili.com/",
        },
    )
    response.raise_for_status()
    try:
        result = AccountResponse.model_validate_json(response.content)
    except ValidationError:
        raise WorkflowError("BILI_ACCOUNT_RESPONSE_INVALID", "B站账号查询返回格式无效。") from None
    if result.code != 0 or not isinstance(result.data, Account):
        raise WorkflowError("BILI_LOGIN_REQUIRED", "B站未确认此 Cookie 已登录。")
    return result.data


def protect_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
        return
    identity = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()
    if not re.fullmatch(r"S-1-\d+(?:-\d+)+", identity):
        raise WorkflowError("PRIVATE_DIRECTORY_FAILED", "无法限制登录态目录权限。")
    subprocess.run(
        ["icacls", str(directory), "/inheritance:r", "/grant:r", f"*{identity}:(OI)(CI)F"],
        capture_output=True,
        timeout=15,
        check=True,
    )


def import_cookie(
    source: Path, destination: Path, executable: Path, client: httpx.Client
) -> Account:
    cookies = load_export(source)
    header = "; ".join(f"{row.name}={row.value}" for row in cookies)
    expected = account(client, header)
    uid = next(row.value for row in cookies if row.name == "DedeUserID")
    if uid != str(expected.mid):
        raise WorkflowError("BILI_ACCOUNT_MISMATCH", "Cookie 账号与登录回查不一致。")
    protect_directory(destination.parent)
    candidate = destination.with_name(f"import-{uuid4().hex}.json")
    session = matching_app_session(destination, expected.mid)
    session["cookie_info"] = exported_cookie_info(cookies)
    try:
        with candidate.open("x", encoding="utf-8") as stream:
            json.dump(session, stream)
        checked = subprocess.run(
            [str(executable), "-u", str(candidate), "list", "--max-pages", "1"],
            cwd=executable.parent,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if checked.returncode:
            raise WorkflowError(
                cli_failure(checked.stderr + checked.stdout),
                "投稿工具未能使用新登录态，原导出文件保留。",
            )
        header, _ = read_credentials(candidate)
        verified = account(client, header)
        if verified.mid != expected.mid:
            raise WorkflowError("BILI_ACCOUNT_MISMATCH", "投稿工具登录账号与原 Cookie 不一致。")
        candidate.replace(destination)
        destination.with_name("verified.json").write_text(
            json.dumps(
                {
                    "account_id": verified.mid,
                    "account_name": verified.uname,
                    "verified_at": int(time.time()),
                }
            ),
            encoding="utf-8",
        )
        return verified
    finally:
        candidate.unlink(missing_ok=True)


def matching_app_session(destination: Path, expected_id: int) -> dict[str, JsonValue]:
    if not destination.is_file():
        raise WorkflowError(
            "BILI_APP_LOGIN_REQUIRED",
            "网页登录已验证；固定版投稿工具另需同账号的 App 登录态，请先完成新账号扫码登录。原 TXT 保留。",
        )
    session = TypeAdapter(dict[str, JsonValue]).validate_json(destination.read_bytes())
    token = session.get("token_info")
    if (
        not isinstance(token, dict)
        or token.get("mid") != expected_id
        or not token.get("access_token")
    ):
        raise WorkflowError(
            "BILI_ACCOUNT_MISMATCH", "App 登录态与导出 Cookie 的账号不一致，拒绝混用。"
        )
    return session


def exported_cookie_info(cookies: list[BrowserCookie]) -> dict[str, JsonValue]:
    return {
        "cookies": [
            {
                "name": row.name,
                "value": row.value,
                "expires": int(row.expirationDate or time.time() + 86400),
                "http_only": int(row.httpOnly),
                "secure": int(row.secure),
                "same_site": 0,
            }
            for row in cookies
        ],
        "domains": [".bilibili.com"],
    }


def cli_failure(output: bytes) -> str:
    missing = re.search(rb"missing field [`']([a-z_]+)[`']", output)
    if missing:
        return "BILI_CLI_MISSING_" + missing[1].decode().upper()
    expected = re.search(rb"expected ([A-Za-z0-9_ ]{1,60})", output)
    if expected:
        return "BILI_CLI_EXPECTED_" + expected[1].decode().strip().replace(" ", "_").upper()
    for label, marker in [
        ("TOKEN", b"token"),
        ("LOGIN", "登录".encode()),
        ("REQUEST", b"request"),
        ("CERTIFICATE", b"certificate"),
    ]:
        if marker in output:
            return "BILI_CLI_" + label + "_FAILED"
    return "BILI_CLI_LOGIN_FAILED"
