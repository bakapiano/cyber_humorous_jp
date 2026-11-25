import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError


class RecommendationAvailability(BaseModel):
    enabled: bool = False


WORKFLOW_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    root: Path = WORKFLOW_ROOT
    testing: bool = False
    automation_enabled: bool = False
    publication_enabled: bool = False
    youtube_home_enabled: bool = False

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.root / 'data/workflow.sqlite3').as_posix()}"

    @property
    def source_manifest(self) -> Path:
        return self.root / "config/sources.json"

    @property
    def key_path(self) -> Path:
        return self.root.parent / "0_youtube_collector/.secrets/youtube_api_key.txt"

    @property
    def artifact_root(self) -> Path:
        return self.root / "artifacts"

    @property
    def bilibili_credentials(self) -> Path:
        return self.root / ".secrets/bilibili/session.json"

    def bilibili_ready(self) -> bool:
        return (
            self.bilibili_credentials.is_file()
            and self.bilibili_credentials.with_name("verified.json").is_file()
        )

    @property
    def node_token_path(self) -> Path:
        return self.root / ".secrets/youtube-service-token.txt"

    @property
    def recommendations_enabled(self) -> bool:
        marker = self.root / "data/youtube-connection.json"
        if not marker.exists():
            return self.youtube_home_enabled
        try:
            return RecommendationAvailability.model_validate_json(marker.read_bytes()).enabled
        except (OSError, ValidationError):
            return False


def get_settings() -> Settings:
    return Settings(
        root=Path(os.environ.get("WORKFLOW_ROOT", str(WORKFLOW_ROOT))).resolve(),
        automation_enabled=os.environ.get("WORKFLOW_AUTOMATION") == "1",
        publication_enabled=os.environ.get("WORKFLOW_LIVE_PUBLICATION") == "1",
        youtube_home_enabled=bool(os.environ.get("YOUTUBE_COOKIE_HEADER_FILE")),
    )


def load_api_key(path: Path) -> str:
    value = os.environ.get("YOUTUBE_API_KEY", "")
    if not value and path.is_file():
        value = path.read_text(encoding="utf-8-sig")
    match = re.search(r"AIza[\w-]{30,50}", value)
    if not match:
        raise ValueError("YOUTUBE_API_KEY_NOT_CONFIGURED")
    return match.group(0)


def existing_proxy(settings: Settings) -> str | None:
    override = os.environ.get("WORKFLOW_PROXY")
    if override:
        return override
    path = settings.root.parent / "0_youtube_collector/data/collector.sqlite3"
    if not path.is_file():
        return None
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT value FROM settings WHERE key='proxy_port'").fetchone()
    if row and 0 < int(row[0]) < 65536:
        return f"http://127.0.0.1:{int(row[0])}"
    return None
