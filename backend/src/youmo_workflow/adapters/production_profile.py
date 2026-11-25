from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from youmo_workflow.adapters.packaging_models import RenderSettings
from youmo_workflow.adapters.production_files import encode, frozen_text


class ProductionProfile:
    def __init__(self, workspace: Path) -> None:
        self.packaging = workspace / "3_标题和封面生成"
        self.editing = workspace / "4剪辑"
        self.poster = workspace / "workflow/config/cover-poster-v001"
        self.raw_titles = workspace / "workflow/config/title-raw-history-v001"

    def cover_prompt(self, payload: dict[str, JsonValue]) -> str:
        return frozen_text(self.raw_titles / "cover_plan.txt").replace(
            "{{input_json}}", encode(payload)
        )

    def vision_prompt(self, payload: dict[str, JsonValue]) -> str:
        return frozen_text(self.poster / "vision.txt").replace("{{input_json}}", encode(payload))

    def title_prompt(self, payload: dict[str, JsonValue]) -> str:
        examples = TypeAdapter(JsonValue).validate_json(
            frozen_text(self.raw_titles / "examples.json")
        )
        return (
            frozen_text(self.raw_titles / "title.txt")
            .replace("{{raw_titles_json}}", encode(examples))
            .replace("{{video_json}}", encode(payload))
        )

    def edit_prompt(self, payload: dict[str, JsonValue]) -> str:
        path = self.editing / "prompt_versions/v003_仅时间区间/plan_prompt.txt"
        return frozen_text(path).replace("{{input_json}}", encode(payload))

    def hard_edit_prompt(self, payload: dict[str, JsonValue]) -> str:
        path = self.poster.parent / "editing-v004-hardsub/plan_prompt.txt"
        return frozen_text(path).replace("{{input_json}}", encode(payload))

    def legacy_edit_prompt(self, payload: dict[str, JsonValue]) -> str:
        path = self.editing / "prompt_versions/v002_标题对齐/plan_prompt.txt"
        return frozen_text(path).replace("{{input_json}}", encode(payload))

    def renderer(self, *, poster: bool = False) -> RenderSettings:
        path = (
            self.poster / "render.json"
            if poster
            else self.packaging / "render_versions/r002_中部避让/settings.json"
        )
        return RenderSettings.model_validate_json(frozen_text(path))
