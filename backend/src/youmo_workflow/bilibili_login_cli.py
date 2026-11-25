import argparse
import json
from pathlib import Path

import httpx

from youmo_workflow.adapters.bilibili_login import import_cookie
from youmo_workflow.config import get_settings
from youmo_workflow.domain.errors import WorkflowError


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import explicitly supplied Bilibili browser cookie JSON"
    )
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    executable = (
        settings.root.parent / "3_bilibili_upload/bin/biliupR-v1.3.4-x86_64-windows/biliup.exe"
    )
    try:
        with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
            result = import_cookie(args.source, settings.bilibili_credentials, executable, client)
        print(
            json.dumps(
                {"imported": True, "account_id": result.mid, "account_name": result.uname},
                ensure_ascii=True,
            )
        )
    except WorkflowError as error:
        print(json.dumps({"error": error.code, "message": error.message}, ensure_ascii=True))
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as error:
        print(
            json.dumps(
                {
                    "error": "BILI_HTTP_FAILED",
                    "status": error.response.status_code,
                    "endpoint": error.request.url.path,
                }
            )
        )
        raise SystemExit(1) from None
    except Exception as error:
        print(
            json.dumps(
                {
                    "error": "BILI_IMPORT_FAILED",
                    "category": type(error).__name__,
                    "message": "Import failed; original file preserved.",
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
