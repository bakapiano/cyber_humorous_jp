from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, StrictInt, ValidationError

from youmo_workflow.domain.usage import TokenUsage, UsageEvent


class CacheDetails(BaseModel):
    cached_tokens: StrictInt | None = Field(default=None, ge=0)


class OutputDetails(BaseModel):
    reasoning_tokens: StrictInt = Field(default=0, ge=0)


class RawUsage(BaseModel):
    prompt_tokens: StrictInt = Field(ge=0)
    completion_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)
    prompt_cache_hit_tokens: StrictInt | None = Field(default=None, ge=0)
    prompt_cache_miss_tokens: StrictInt | None = Field(default=None, ge=0)
    prompt_tokens_details: CacheDetails | None = None
    completion_tokens_details: OutputDetails | None = None

    def tokens(self) -> TokenUsage:
        hit = self.prompt_cache_hit_tokens
        if hit is None and self.prompt_tokens_details:
            hit = self.prompt_tokens_details.cached_tokens
        miss = self.prompt_cache_miss_tokens
        if hit is not None and miss is None:
            miss = self.prompt_tokens - hit
        if miss is not None and hit is None:
            hit = self.prompt_tokens - miss
        if (
            hit is not None
            and miss is not None
            and (min(hit, miss) < 0 or hit + miss != self.prompt_tokens)
        ):
            raise ValueError("Invalid cache split")
        total = self.prompt_tokens + self.completion_tokens
        reasoning = (
            self.completion_tokens_details.reasoning_tokens if self.completion_tokens_details else 0
        )
        if (
            self.total_tokens is not None and self.total_tokens != total
        ) or reasoning > self.completion_tokens:
            raise ValueError("Invalid token total")
        return TokenUsage(self.prompt_tokens, self.completion_tokens, hit, miss, reasoning)


class RawResponse(BaseModel):
    id: str | None = None
    model: str = "unknown"
    created: StrictInt | None = None
    usage: RawUsage | None = None


class BatchReference(BaseModel):
    batch_id: str


class SelectionReference(BaseModel):
    request: BatchReference
    request_sha256: str
    prompt_sha256: str


@dataclass(frozen=True)
class ResponseMetadata:
    identifier: str | None
    model: str
    at: datetime | None
    usage: TokenUsage | None


class UsageJournal:
    """Read only physical call journals, never request model text or credentials."""

    def __init__(self, data_root: Path):
        self.root = data_root
        self.cache: dict[Path, tuple[int, int, ResponseMetadata]] = {}

    def snapshot(self) -> tuple[list[UsageEvent], int]:
        bindings = self.selection_bindings()
        entries: dict[str, UsageEvent] = {}
        duplicates = 0
        for directory in sorted(self.call_directories()):
            raw = directory / "provider-response.json"
            fallback = directory / "response.json"
            response = self.read(raw if raw.exists() else fallback)
            relative = directory.relative_to(self.root)
            identity = response.identifier or "unidentified:" + relative.as_posix()
            if identity in entries:
                duplicates += 1
                continue
            content, batch, stage = self.context(directory, bindings)
            entries[identity] = UsageEvent(
                identity,
                response.model,
                response.at,
                response.usage,
                content,
                batch,
                stage,
                retry="corrections" in relative.parts
                or (directory.parent.name == "network" and directory.name != "00"),
            )
        return list(entries.values()), duplicates

    def call_directories(self) -> set[Path]:
        directories: set[Path] = set()
        for root in (self.root / "production", self.root / "selection"):
            for path in root.rglob("provider-response.json"):
                if self.safe(path):
                    directories.add(path.parent)
            for path in root.rglob("attempt.json"):
                directory = path.parent
                physical = (
                    not (directory / "network").exists()
                    or (directory / "network/legacy-initial.json").exists()
                )
                if self.safe(path) and physical and (directory / "payload.json").exists():
                    directories.add(directory)
        return directories

    def safe(self, path: Path) -> bool:
        return not path.is_symlink() and path.resolve().is_relative_to(self.root.resolve())

    def read(self, path: Path) -> ResponseMetadata:
        empty = ResponseMetadata(None, "unknown", None, None)
        if not self.safe(path):
            return empty
        try:
            stat = path.stat()
            cached = self.cache.get(path)
            if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                return cached[2]
            raw = RawResponse.model_validate_json(path.read_bytes())
            at = datetime.fromtimestamp(raw.created, UTC) if raw.created is not None else None
            value = ResponseMetadata(
                raw.id, raw.model, at, raw.usage.tokens() if raw.usage else None
            )
            self.cache[path] = (stat.st_mtime_ns, stat.st_size, value)
            return value
        except (OSError, ValueError, OverflowError):
            return empty

    def selection_bindings(self) -> dict[Path, tuple[str, str]]:
        bindings: dict[Path, tuple[str, str]] = {}
        for path in (self.root / "selection").rglob("request.json"):
            if not self.safe(path):
                continue
            try:
                call = SelectionReference.model_validate_json(path.read_bytes())
            except (OSError, ValidationError):
                continue
            stage = "初筛补漏" if "rescue" in path.parts else "标题初筛"
            context = (call.request.batch_id, stage)
            bindings[path.parent] = context
            bindings[self.root / "selection/raw" / call.request_sha256 / call.prompt_sha256] = (
                context
            )
        return bindings

    def context(
        self, directory: Path, bindings: dict[Path, tuple[str, str]]
    ) -> tuple[str | None, str | None, str]:
        parts = directory.relative_to(self.root).parts
        if parts[0] == "production" and len(parts) >= 3:
            return parts[1], None, parts[2]
        for parent in (directory, *directory.parents):
            if parent in bindings:
                batch, stage = bindings[parent]
                return None, batch, stage
        return None, None, "未归属选题"
