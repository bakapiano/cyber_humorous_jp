import msvcrt
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from youmo_workflow.domain.errors import WorkflowError


@contextmanager
def model_lock(path: Path) -> Generator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise WorkflowError(
                "MODEL_REQUEST_IN_FLIGHT", "相同模型请求仍在执行，未并发重发。"
            ) from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
