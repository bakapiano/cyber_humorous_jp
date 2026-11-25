import argparse
import json
from dataclasses import replace

from youmo_workflow.adapters.biliup import BiliupGateway
from youmo_workflow.config import get_settings
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.features.production import confirm_reprint, enqueue_video
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import build_runtime


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicit single-video production and private reprint"
    )
    parser.add_argument(
        "command",
        choices=[
            "produce",
            "status",
            "confirm-reprint",
            "submit-private",
            "verify",
            "attach-subtitles",
            "recover-receipt",
        ],
    )
    parser.add_argument("identity", nargs="?", default="")
    parser.add_argument("--manifest-hash", default="")
    parser.add_argument("--remote-id", default="")
    parser.add_argument("--confirm-model-calls", action="store_true")
    parser.add_argument("--confirm-reprint", action="store_true")
    parser.add_argument("--confirm-private-upload", action="store_true")
    args = parser.parse_args()
    runtime = build_runtime(get_settings())
    remote_root = runtime.settings.root.parent / "3_bilibili_upload"
    gateway = BiliupGateway(
        remote_root / "bin/biliupR-v1.3.4-x86_64-windows/biliup.exe",
        runtime.settings.bilibili_credentials,
        runtime.settings.artifact_root / "publication_receipts",
    )
    if args.command == "produce":
        if not args.confirm_model_calls:
            raise WorkflowError(
                "CONFIRMATION_REQUIRED",
                "单条制作正常5次调用，含格式纠正及网络恢复最多45次，须明确确认。",
            )
        print(json.dumps(enqueue_video(runtime, args.identity).model_dump(), ensure_ascii=True))
    elif args.command == "status":
        print(
            json.dumps(
                {
                    "tasks": [row.model_dump() for row in runtime.tasks.list_tasks()],
                    "publications": [
                        row.model_dump() for row in runtime.publications.list_publications()
                    ],
                },
                ensure_ascii=True,
            )
        )
    elif args.command == "confirm-reprint":
        if not args.confirm_reprint or not args.manifest_hash:
            raise WorkflowError("CONFIRMATION_REQUIRED", "须确认转载及具体发布包摘要。")
        print(
            json.dumps(
                confirm_reprint(runtime, args.identity, args.manifest_hash).model_dump(),
                ensure_ascii=True,
            )
        )
    elif args.command == "recover-receipt":
        print(
            json.dumps(
                {
                    "result": PublicationExecutor(runtime, gateway).recover_receipt(
                        args.identity, args.remote_id
                    )
                }
            )
        )
    else:
        if args.command != "verify":
            if not args.confirm_private_upload:
                raise WorkflowError("CONFIRMATION_REQUIRED", "须明确确认本次私密投稿或字幕写入。")
            runtime.settings = replace(runtime.settings, publication_enabled=True)
        executor = PublicationExecutor(runtime, gateway)
        actions = {
            "submit-private": executor.submit_private,
            "verify": executor.reconcile,
            "attach-subtitles": executor.attach_subtitles,
        }
        print(json.dumps({"result": actions[args.command](args.identity)}))


if __name__ == "__main__":
    try:
        main()
    except WorkflowError as error:
        print(json.dumps({"error": error.code, "message": error.message}, ensure_ascii=True))
        raise SystemExit(1) from None
