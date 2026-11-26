from pydantic import BaseModel, ConfigDict, Field

from src.constants.workflow import Decision


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    decision: Decision
    note: str = Field(min_length=4, max_length=1000)


class SwitchInfo(BaseModel):
    stale_group_ids: list[str]
    effective_group_ids: list[str]
    quota_remaining_after_switch: int


class ReviewInfo(ReviewCreate):
    run_id: int
    created_at: str
    switch: SwitchInfo | None = None
