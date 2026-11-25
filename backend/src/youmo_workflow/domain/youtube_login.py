from typing import Literal

from youmo_workflow.domain.models import Contract


class YoutubeConnection(Contract):
    status: Literal[
        "disconnected", "paired", "received", "checking", "connected", "expired", "error"
    ]
    paired: bool
    source: Literal["extension", "file", "none"]
    last_sync: str | None = None
    last_verified: str | None = None
    account_name: str | None = None
    homepage_count: int = 0
    message: str


class YoutubePairing(Contract):
    code: str
    expires_at: str
    extension_id: str
