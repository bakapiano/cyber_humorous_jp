import shutil

from youmo_workflow.config import get_settings

MIN_FREE_BYTES = 2 * 1024**3


def disk_ready() -> bool:
    return shutil.disk_usage(get_settings().root).free >= MIN_FREE_BYTES
