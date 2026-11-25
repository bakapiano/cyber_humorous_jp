import httpx
from pydantic import ValidationError

from youmo_workflow.adapters.youtube_duration import duration_seconds
from youmo_workflow.adapters.youtube_models import (
    ChannelPage,
    PlaylistItem,
    PlaylistPage,
    VideoMetadataPage,
)
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import DiscoveryVideo
from youmo_workflow.domain.time import parse_stamp, stamp


class GoogleChannelProvider:
    def __init__(self, key: str, client: httpx.Client, max_pages: int = 20) -> None:
        self.key = key
        self.client = client
        self.max_pages = max_pages

    def request(self, resource: str, params: dict[str, str]) -> bytes:
        try:
            response = self.client.get(
                f"https://www.googleapis.com/youtube/v3/{resource}",
                params={**params, "key": self.key},
            )
        except httpx.HTTPError:
            raise WorkflowError("UPSTREAM_UNAVAILABLE", "YouTube 元数据连接失败。", True) from None
        if response.status_code != 200:
            retryable = response.status_code >= 500 or response.status_code == 429
            raise WorkflowError(
                "YOUTUBE_API_ERROR",
                f"YouTube 元数据请求失败（HTTP {response.status_code}）。",
                retryable,
            )
        return response.content

    def collect(self, handle: str, since: str) -> list[DiscoveryVideo]:
        try:
            return self.collect_validated(handle, since)
        except (ValidationError, ValueError):
            raise WorkflowError(
                "UPSTREAM_PARSE_ERROR", "YouTube 元数据结构或时间格式无效。"
            ) from None

    def lookup(self, video_ids: list[str]) -> list[DiscoveryVideo]:
        result: list[DiscoveryVideo] = []
        for offset in range(0, len(video_ids), 50):
            data = self.request(
                "videos",
                {"part": "snippet,contentDetails", "id": ",".join(video_ids[offset : offset + 50])},
            )
            page = VideoMetadataPage.model_validate_json(data)
            for item in page.items:
                snippet = item.snippet
                image = snippet.thumbnails.get("high") or snippet.thumbnails.get("default")
                result.append(
                    DiscoveryVideo(
                        video_id=item.id,
                        title=snippet.title,
                        channel_id=snippet.channelId,
                        channel_title=snippet.channelTitle,
                        published_at=stamp(parse_stamp(snippet.publishedAt)),
                        duration_seconds=duration_seconds(item.contentDetails.duration),
                        thumbnail_url=image.url if image else None,
                        origin="home",
                    )
                )
        return result

    def collect_validated(self, handle: str, since: str) -> list[DiscoveryVideo]:
        selector = "id" if handle.startswith("UC") else "forHandle"
        channel = ChannelPage.model_validate_json(
            self.request("channels", {"part": "snippet,contentDetails", selector: handle})
        )
        if not channel.items:
            raise WorkflowError("SOURCE_NOT_FOUND", "来源频道未返回可用元数据。")
        item = channel.items[0]
        playlist = item.contentDetails.relatedPlaylists.uploads
        videos = self.scan_playlist(playlist, item.id, since)
        durations = {
            row.video_id: row.duration_seconds
            for row in self.lookup([row.video_id for row in videos])
        }
        return [
            row.model_copy(update={"duration_seconds": durations.get(row.video_id)})
            for row in videos
        ]

    def scan_playlist(self, playlist: str, channel_id: str, since: str) -> list[DiscoveryVideo]:
        params = {"part": "snippet,contentDetails", "playlistId": playlist, "maxResults": "50"}
        result: dict[str, DiscoveryVideo] = {}
        tokens: set[str] = set()
        cutoff = parse_stamp(since)
        for _ in range(self.max_pages):
            page = PlaylistPage.model_validate_json(self.request("playlistItems", params))
            normalized = [normalize(item, channel_id) for item in page.items]
            dated = [item for item in normalized if item is not None]
            for item in dated:
                if item.published_at and parse_stamp(item.published_at) >= cutoff:
                    result[item.video_id] = item
            if not page.nextPageToken or page_is_older(dated, since):
                return list(result.values())
            if page.nextPageToken in tokens:
                raise WorkflowError("PAGINATION_LOOP", "来源分页重复，未推进成功游标。")
            tokens.add(page.nextPageToken)
            params["pageToken"] = page.nextPageToken
        raise WorkflowError("SCAN_LIMIT_REACHED", "本轮分页达到上限，未推进成功游标。")


def page_is_older(items: list[DiscoveryVideo], since: str) -> bool:
    dates = [item.published_at for item in items if item.published_at]
    return bool(dates) and all(parse_stamp(date) < parse_stamp(since) for date in dates)


def normalize(item: PlaylistItem, channel_id: str) -> DiscoveryVideo | None:
    published = item.contentDetails.videoPublishedAt
    if not published or not item.snippet.title:
        return None
    thumbs = item.snippet.thumbnails
    image = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default")
    return DiscoveryVideo(
        video_id=item.contentDetails.videoId,
        title=item.snippet.title,
        channel_id=item.snippet.videoOwnerChannelId or channel_id,
        channel_title=item.snippet.channelTitle,
        published_at=stamp(parse_stamp(published)),
        thumbnail_url=image.url if image else None,
    )
