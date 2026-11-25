"""Refresh public channel avatar assets; never changes workflow or account state."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from youmo_workflow.adapters.youtube import GoogleChannelProvider
from youmo_workflow.adapters.youtube_models import Snippet
from youmo_workflow.config import existing_proxy, get_settings, load_api_key
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.features.sources import known_channel_ids


class AvatarChannel(BaseModel):
    id: str
    snippet: Snippet


class AvatarPage(BaseModel):
    items: list[AvatarChannel] = Field(default_factory=lambda: list[AvatarChannel]())


def allowed_image(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and port in (None, 443)
        and not parsed.username
        and not parsed.password
        and any(
            host == domain or host.endswith(f".{domain}")
            for domain in ("ggpht.com", "googleusercontent.com", "ytimg.com")
        )
    )


def cache_image(client: httpx.Client, channel: AvatarChannel, output: Path) -> bool:
    thumbnail = channel.snippet.thumbnails.get("default")
    if not thumbnail or not allowed_image(thumbnail.url):
        return False
    try:
        with client.stream("GET", thumbnail.url) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 2_000_000:
                    return False
        with Image.open(BytesIO(content)) as image:
            if image.width > 2048 or image.height > 2048:
                return False
            image.thumbnail((96, 96))
            image.convert("RGBA").save(output, format="PNG")
        return True
    except (httpx.HTTPError, OSError, UnidentifiedImageError):
        return False


def refresh() -> None:
    settings = get_settings()
    known = known_channel_ids(settings.source_manifest)
    ids = [
        channel_id for channel_id in known if re.fullmatch(r"UC[\w-]{22}", channel_id)
    ]
    public = settings.root / "frontend/public"
    images = public / "source-avatars"
    images.mkdir(parents=True, exist_ok=True)
    avatars: dict[str, str] = {}
    with httpx.Client(
        proxy=existing_proxy(settings), timeout=30, follow_redirects=False
    ) as client:
        provider = GoogleChannelProvider(load_api_key(settings.key_path), client)
        channels: list[AvatarChannel] = []
        for offset in range(0, len(ids), 50):
            page = AvatarPage.model_validate_json(
                provider.request(
                    "channels",
                    {"part": "snippet", "id": ",".join(ids[offset : offset + 50])},
                )
            )
            channels.extend(channel for channel in page.items if channel.id in ids)

        def save(channel: AvatarChannel) -> tuple[str, bool]:
            return channel.id, cache_image(
                client, channel, images / f"{channel.id}.png"
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            for channel_id, success in pool.map(save, channels):
                if success or (images / f"{channel_id}.png").is_file():
                    avatars[known[channel_id]] = f"/source-avatars/{channel_id}.png"
    payload = {"refreshed_at": datetime.now(UTC).isoformat(), "avatars": avatars}
    (public / "source-avatars.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Cached public channel avatars: {len(avatars)}/{len(known)}")


if __name__ == "__main__":
    try:
        refresh()
    except (WorkflowError, ValueError, OSError):
        raise SystemExit(
            "Avatar refresh failed; no account or workflow state was changed."
        ) from None
