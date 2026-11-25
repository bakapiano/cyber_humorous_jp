import argparse
import io
import json
import secrets
import sys
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from youmo_workflow.api import create_app
from youmo_workflow.config import get_settings
from youmo_workflow.domain.batch_plan import BatchTrigger
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.discovery import enqueue_discovery
from youmo_workflow.runtime import build_runtime, initialize


def export_contract(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    cast("io.TextIOWrapper[io.BufferedWriter]", sys.stdout).reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Youmo local workflow")
    parser.add_argument("command", choices=["init", "sync", "batch", "status", "export-openapi"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm-execution", action="store_true")
    parser.add_argument("--request-id", type=UUID)
    args = parser.parse_args()
    if args.command == "export-openapi":
        export_contract(args.output or Path("contracts/openapi.json"))
        return
    runtime = build_runtime(get_settings())
    initialize(runtime)
    if args.command == "init":
        token = runtime.settings.node_token_path
        token.parent.mkdir(parents=True, exist_ok=True)
        if not token.exists():
            with token.open("x", encoding="utf-8") as file:
                file.write(secrets.token_urlsafe(48))
        print(json.dumps({"sources": len(runtime.sources.list_sources()), "initialized": True}))
    elif args.command == "sync":
        print(
            json.dumps(
                [
                    item.model_dump()
                    for item in enqueue_discovery(runtime.factory, policy=runtime.policy)
                ],
                ensure_ascii=False,
            )
        )
    elif args.command == "batch":
        if not args.confirm_execution:
            parser.error(
                "batch requires --confirm-execution: paid production and private reprint uploads"
            )
        plan = BatchPlanService(runtime.factory, runtime.policy).state()
        result = BatchTriggerService(
            runtime.factory,
            runtime.policy,
            publication_ready=runtime.settings.bilibili_ready,
        ).trigger(
            BatchTrigger(
                request_id=args.request_id or uuid4(),
                revision=plan.revision,
                confirm_execution=True,
            )
        )
        assert result is not None
        print(result.model_dump_json())
    else:
        print(
            json.dumps(
                {
                    "sources": len(runtime.sources.list_sources()),
                    "tasks": [item.model_dump() for item in runtime.tasks.list_tasks()],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
