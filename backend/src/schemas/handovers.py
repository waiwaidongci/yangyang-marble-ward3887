from pydantic import BaseModel, ConfigDict, Field


class HandoverCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    base_run_id: int = Field(gt=0)
    max_kits: int | None = Field(default=None, gt=0, le=60)


class HandoverInfo(BaseModel):
    id: int
    base_run_id: int
    dataset_id: int
    created_at: str
    max_kits: int
    active_run_id: int
    sealed_kit_count: int
    sealed_piece_count: int
    remaining_kits: int
    sealed_piece_ids: list[str]
    switch_log: list


class SealCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    handover_id: int = Field(gt=0)
    group_id: str = Field(min_length=2, max_length=40)
    note: str = Field(default='', max_length=1000)


class SealedBagInfo(BaseModel):
    id: int
    handover_id: int
    run_id: int
    group_id: str
    piece_ids: list[str]
    piece_count: int
    sealed_at: str
    note: str


class RecheckCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    handover_id: int = Field(gt=0)
    label: str = Field(min_length=2, max_length=140)
    csv: str = Field(min_length=1)


class RecheckInfo(BaseModel):
    id: int
    phase: int
    handover_id: int
    remaining_kits: int
    status: str
