import time

import httpx

from youmo_workflow.adapters.blackboxes import UnimplementedPackaging, UnimplementedSelection
from youmo_workflow.adapters.home import NodeHomeProvider
from youmo_workflow.adapters.youtube import GoogleChannelProvider
from youmo_workflow.config import existing_proxy, get_settings, load_api_key
from youmo_workflow.features.scan_plan import SCAN_KINDS
from youmo_workflow.features.sources import known_channel_ids
from youmo_workflow.orchestration.runner import Providers, Runner
from youmo_workflow.orchestration.scan_scheduler import ScanScheduler
from youmo_workflow.runtime import build_runtime, initialize


def main() -> None:
    runtime = build_runtime(get_settings())
    initialize(runtime)
    with httpx.Client(
        timeout=25, proxy=existing_proxy(runtime.settings), trust_env=False
    ) as remote:
        with httpx.Client(timeout=45, trust_env=False) as local:
            providers = Providers(
                GoogleChannelProvider(load_api_key(runtime.settings.key_path), remote),
                UnimplementedSelection(),
                UnimplementedPackaging(),
                runtime.publications,
            )
            providers.home = NodeHomeProvider(runtime.settings.node_token_path, local)
            runner = Runner(runtime.factory, providers, runtime.artifacts)
            runner.task_kinds = SCAN_KINDS
            runner.known_channels = known_channel_ids(runtime.settings.source_manifest)
            scheduler = ScanScheduler(runtime.factory, runtime.policy)
            while True:
                scheduler.home_enabled = runtime.settings.recommendations_enabled
                scheduler.tick()
                if not runner.once():
                    time.sleep(2)


if __name__ == "__main__":
    main()
