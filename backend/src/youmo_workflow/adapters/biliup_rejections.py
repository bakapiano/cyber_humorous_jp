"""Recognize explicit submit rejection only, never infer failure from missing BV."""

import re


def submit_rate_limited(return_code: int, stdout: str, stderr: str) -> bool:
    text = stdout + stderr
    if return_code != 1 or re.search(r"BV[A-Za-z0-9]{10}|code:\s*0\b|data:\s*Some", text):
        return False
    responses = re.findall(
        r'ResponseData\s*\{\s*code:\s*(-?\d+),\s*data:\s*None,\s*message:\s*"([^"\r\n]*)"',
        text,
    )
    return bool(responses) and all(
        code == "137022" and message == "投稿过于频繁，请稍后再试" for code, message in responses
    )
