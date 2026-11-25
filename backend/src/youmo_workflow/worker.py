import argparse
import time

import httpx

from youmo_workflow.adapters.biliup import BiliupGateway
from youmo_workflow.adapters.blackboxes import UnimplementedSelection
from youmo_workflow.adapters.home import NodeHomeProvider
from youmo_workflow.adapters.initial_selection import V004InitialSelector
from youmo_workflow.adapters.model_http import load_retry_policy
from youmo_workflow.adapters.model_route import ModelRoute, load_model_route
from youmo_workflow.adapters.rescue_profile import V008Profile
from youmo_workflow.adapters.rescue_selection import V008InitialSelector
from youmo_workflow.adapters.selection_completion import DeepSeekCompletion
from youmo_workflow.adapters.selection_journal import SelectionJournal
from youmo_workflow.adapters.selection_passthrough import PassthroughRescreen
from youmo_workflow.adapters.selection_profile import V004Profile
from youmo_workflow.adapters.youtube import GoogleChannelProvider
from youmo_workflow.config import existing_proxy, get_settings, load_api_key
from youmo_workflow.domain.pipeline import stage_assignments
from youmo_workflow.execution_limits import resource_budget
from youmo_workflow.features.production_buffer import PRODUCER_KINDS
from youmo_workflow.features.sources import known_channel_ids
from youmo_workflow.orchestration.backlog_control import BacklogController
from youmo_workflow.orchestration.batch_publication import BatchPublisher
from youmo_workflow.orchestration.cleanup import BatchMediaCleanup
from youmo_workflow.orchestration.finalization import PrivateFinalizer
from youmo_workflow.orchestration.parallel_worker import Worker, serve_parallel
from youmo_workflow.orchestration.runner import Providers, Runner
from youmo_workflow.orchestration.scheduler import Scheduler
from youmo_workflow.orchestration.selection import SelectionProviders, SelectionStages
from youmo_workflow.production_runtime import build_production
from youmo_workflow.runtime import Runtime, build_runtime, initialize


def build_runner(runtime: Runtime, client: httpx.Client, local_client: httpx.Client) -> Runner:
    key = load_api_key(runtime.settings.key_path)
    root = runtime.settings.root
    route = load_model_route(root)
    model_client = local_client if route is ModelRoute.WORKBUDDY else client
    completion = DeepSeekCompletion(
        model_client,
        route.credential_path(root),
        root / "data/selection/raw",
        load_retry_policy(root),
        route=route,
    )
    content, packaging, editor = build_production(
        runtime,
        local_client,
        model_client=model_client,
        model_route=route,
        cover_fallback_client=client,
    )
    providers = Providers(
        GoogleChannelProvider(key, client),
        UnimplementedSelection(),
        packaging,
        runtime.publications,
    )
    providers.home = NodeHomeProvider(runtime.settings.node_token_path, local_client)
    providers.editor = editor
    providers.content = content
    runner = Runner(runtime.factory, providers, runtime.artifacts)
    bili_root = runtime.settings.root.parent / "3_bilibili_upload"
    gateway = BiliupGateway(
        bili_root / "bin/biliupR-v1.3.4-x86_64-windows/biliup.exe",
        runtime.settings.bilibili_credentials,
        runtime.settings.artifact_root / "publication_receipts",
    )
    runner.finalize_private = PrivateFinalizer(runtime, gateway).run
    publisher = BatchPublisher(runtime, gateway)
    runner.publish_private, runner.authorize_publication = publisher.run, publisher.authorize
    runner.cleanup_batch = BatchMediaCleanup(runtime).run
    runner.known_channels = known_channel_ids(runtime.settings.source_manifest)
    runner.selection_stages = SelectionStages(
        runtime.factory,
        SelectionProviders(
            V004InitialSelector(
                V004Profile(runtime.settings.root.parent / "1_选题"),
                SelectionJournal(runtime.settings.root / "data/selection"),
                completion,
            ),
            PassthroughRescreen(),
            content,
            V008InitialSelector(
                V008Profile(runtime.settings.root.parent / "1_选题"),
                (
                    SelectionJournal(runtime.settings.root / "data/selection/v008/baseline"),
                    SelectionJournal(runtime.settings.root / "data/selection/v008/rescue"),
                ),
                completion,
            ),
        ),
    )
    return runner


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--drain", action="store_true")
    parser.add_argument("--production-only", action="store_true")
    args = parser.parse_args()
    runtime = build_runtime(get_settings())
    initialize(runtime)
    with httpx.Client(
        timeout=25, proxy=existing_proxy(runtime.settings), trust_env=False
    ) as client:
        with httpx.Client(timeout=45, trust_env=False) as local_client:
            runner = build_runner(runtime, client, local_client)
            if args.production_only:
                runner.task_kinds = PRODUCER_KINDS
            scheduler = Scheduler(
                runtime.factory,
                runtime.policy,
                publication_ready=runtime.settings.bilibili_ready,
            )
            if args.production_only:
                scheduler.backlog_tick = BacklogController(runtime).tick
            policy = resource_budget().policy
            if args.production_only and policy.enabled and not args.once and not args.drain:
                workers: list[Worker] = []
                for index, kinds in enumerate(stage_assignments(policy.workers)):
                    other = runner if index == 0 else build_runner(runtime, client, local_client)
                    other.task_kinds = kinds
                    other.downstream_first = True
                    workers.append(other)
                serve_parallel(
                    workers, scheduler, runtime.settings.root / "data/performance/tasks.jsonl"
                )
                return
            while True:
                scheduler.tick()
                worked = runner.once()
                if args.once or (args.drain and not worked):
                    break
                if not worked:
                    time.sleep(2)


if __name__ == "__main__":
    main()
