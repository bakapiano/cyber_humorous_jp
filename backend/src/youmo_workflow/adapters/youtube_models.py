from pydantic import BaseModel, Field


class Image(BaseModel):
    url: str


class Snippet(BaseModel):
    title: str = ""
    channelTitle: str = ""
    videoOwnerChannelId: str | None = None
    thumbnails: dict[str, Image] = Field(default_factory=dict)


class PlaylistContent(BaseModel):
    videoId: str
    videoPublishedAt: str | None = None


class PlaylistItem(BaseModel):
    snippet: Snippet
    contentDetails: PlaylistContent


class PlaylistPage(BaseModel):
    items: list[PlaylistItem] = Field(default_factory=lambda: list[PlaylistItem]())
    nextPageToken: str | None = None


class RelatedPlaylists(BaseModel):
    uploads: str


class ChannelContent(BaseModel):
    relatedPlaylists: RelatedPlaylists


class ChannelItem(BaseModel):
    id: str
    snippet: Snippet
    contentDetails: ChannelContent


class ChannelPage(BaseModel):
    items: list[ChannelItem] = Field(default_factory=lambda: list[ChannelItem]())


class VideoSnippet(BaseModel):
    title: str
    channelId: str
    channelTitle: str
    publishedAt: str
    thumbnails: dict[str, Image] = Field(default_factory=dict)


class VideoContent(BaseModel):
    duration: str | None = None


class VideoMetadata(BaseModel):
    id: str
    snippet: VideoSnippet
    contentDetails: VideoContent = Field(default_factory=VideoContent)


class VideoMetadataPage(BaseModel):
    items: list[VideoMetadata] = Field(default_factory=lambda: list[VideoMetadata]())
