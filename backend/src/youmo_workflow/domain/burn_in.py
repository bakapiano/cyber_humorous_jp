from typing import Literal

from pydantic import Field

from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.providers import ArtifactRef


class BurnInProof(Contract):
    version: Literal["bilingual_burn_v001"] = "bilingual_burn_v001"
    media_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    subtitle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ass_ref: ArtifactRef
    font_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    font_name: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    cue_count: int = Field(gt=0)
    seconds: float = Field(gt=0, allow_inf_nan=False)
