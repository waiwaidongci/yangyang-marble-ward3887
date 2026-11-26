from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base


class Run(Base):
    __tablename__ = 'analysis_runs'

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(140))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    created_at: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)
    settings: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    # 复测运行（kind='recheck'）通过 handover_id 归属交接批次，phase 从 1 递增；
    # kind 为空或 'initial' 表示普通分析运行。
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    handover_id: Mapped[int | None] = mapped_column(ForeignKey('handovers.id'), nullable=True, index=True)
    phase: Mapped[int | None] = mapped_column(nullable=True)
    parent_run_id: Mapped[int | None] = mapped_column(ForeignKey('analysis_runs.id'), nullable=True)
    # 提交复测瞬间冻结的上下文：已封袋件、剩余名额、父运行冻结标尺。
    recheck_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
