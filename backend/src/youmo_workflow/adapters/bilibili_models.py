from pydantic import BaseModel, Field


class Cookie(BaseModel):
    name: str
    value: str


class CookieInfo(BaseModel):
    cookies: list[Cookie]


class Credentials(BaseModel):
    cookie_info: CookieInfo


class Cue(BaseModel):
    start: float = Field(alias="from")
    end: float = Field(alias="to")
    content: str
    location: int = 2


class SubtitleBody(BaseModel):
    body: list[Cue]


class Track(BaseModel):
    lan: str
    subtitle_url: str


class TrackList(BaseModel):
    subtitles: list[Track] = Field(default_factory=lambda: list[Track]())


class PlayerData(BaseModel):
    subtitle: TrackList = Field(default_factory=TrackList)


class PlayerResponse(BaseModel):
    code: int
    data: PlayerData | None = None


class WriteResponse(BaseModel):
    code: int


class ReviewLink(BaseModel):
    subtitle_id_str: str


class ReviewLanguage(BaseModel):
    lan: str
    audit_back: ReviewLink | None = None


class ReviewLanguages(BaseModel):
    code: int
    data: list[ReviewLanguage] | None = None


class ReviewDetail(BaseModel):
    status: int
    oid: int
    bvid: str
    lan: str
    subtitle_url: str
    upper_status: int
    reject_comment: str = "平台未说明原因"


class ReviewResponse(BaseModel):
    code: int
    data: ReviewDetail | None = None
