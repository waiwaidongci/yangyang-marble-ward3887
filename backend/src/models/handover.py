from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base


class Handover(Base):
    """一次边封袋边复测的交接批次：绑定获批初版运行与套数上限。"""

    __tablename__ = 'handovers'

    id: Mapped[int] = mapped_column(primary_key=True)
    base_run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), unique=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    created_by: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    created_at: Mapped[str] = mapped_column(String(32))
    max_kits: Mapped[int] = mapped_column()
    active_run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'))
    # 每次版本切换的审计快照：阶段、过期组、切换后剩余名额等。
    switch_log: Mapped[list] = mapped_column(JSON, default=list)
