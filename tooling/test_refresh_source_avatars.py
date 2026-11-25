"""Offline regression coverage for the explicit avatar refresh tool."""

from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from .refresh_source_avatars import AvatarChannel, allowed_image, cache_image


@pytest.mark.parametrize(
    "url",
    [
        "http://yt3.ggpht.com/a",
        "https://ggpht.com.evil.example/a",
        "https://127.0.0.1/a",
        "https://user:pass@yt3.ggpht.com/a",
        "https://yt3.ggpht.com:444/a",
        "https://yt3.ggpht.com:invalid/a",
    ],
)
def test_rejects_untrusted_image_hosts(url: str) -> None:
    assert not allowed_image(url)


def test_caches_actual_image_and_refuses_redirects(tmp_path: Path) -> None:
    buffer = BytesIO()
    Image.new("RGB", (100, 100), "blue").save(buffer, format="PNG")
    channel = AvatarChannel.model_validate(
        {
            "id": "UC" + "a" * 22,
            "snippet": {"thumbnails": {"default": {"url": "https://yt3.ggpht.com/a"}}},
        }
    )
    target = tmp_path / "avatar.png"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=buffer.getvalue())
        )
    ) as client:
        assert cache_image(client, channel, target)
    with Image.open(target) as cached:
        assert cached.size == (96, 96)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"Location": "https://example.com"})
        )
    ) as client:
        assert not cache_image(client, channel, tmp_path / "redirect.png")


def test_rejects_non_image_payload(tmp_path: Path) -> None:
    channel = AvatarChannel.model_validate(
        {
            "id": "UC" + "a" * 22,
            "snippet": {"thumbnails": {"default": {"url": "https://yt3.ggpht.com/a"}}},
        }
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"not an image")
        )
    ) as client:
        assert not cache_image(client, channel, tmp_path / "invalid.png")
