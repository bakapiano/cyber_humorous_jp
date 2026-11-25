"""Reject inline lint bypasses in first-party Python and TypeScript."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPES = (
    "backend/src",
    "backend/tests",
    "frontend/src",
    "services/youtube/src",
    "packages/browser-bridge/src",
    "extensions/youtube-login/src",
    "e2e",
)
FORBIDDEN = re.compile(
    r"(?:#|//|/\*)\s*(?:noqa|type:\s*ignore|pyright:\s*ignore|pylint:\s*disable|eslint-disable|@ts-ignore|@ts-nocheck)"
)


def main() -> None:
    failures: list[str] = []
    for scope in SCOPES:
        for path in (ROOT / scope).rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if FORBIDDEN.search(line):
                    failures.append(
                        f"{path.relative_to(ROOT)}:{number}: inline bypass forbidden"
                    )
    if failures:
        raise SystemExit("\n".join(failures))
    print("Policy: no inline lint/type bypasses found.")


if __name__ == "__main__":
    main()
