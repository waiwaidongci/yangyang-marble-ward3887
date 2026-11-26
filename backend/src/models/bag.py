from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base


class SealedBag(Base):
    """封袋确认记录：套件原样交接，成员快照在确认瞬间冻结。"""

    __tablename__ = 'sealed_bags'

    id: Mapped[int] = mapped_column(primary_key=True)
    handover_id: Mapped[int] = mapped_column(ForeignKey('handovers.id'), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), index=True)
    group_id: Mapped[str] = mapped_column(String(40))
    piece_ids: Mapped[list] = mapped_column(JSON)
    piece_count: Mapped[int] = mapped_column()
    sealed_by: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    sealed_at: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default='')
