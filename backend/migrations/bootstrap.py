from sqlalchemy import inspect, text

from src.config.database import Base, engine
from src.models import account, audit, bag, dataset, handover, review, run  # noqa: F401

# 既有部署（如演示卷）可能停留在 v1 表结构，这里按列名做幂等补齐，
# 避免让边封袋边复测功能依赖手工删库。
ADDED_COLUMNS = {
    'analysis_runs': [
        ('kind', "VARCHAR(16) NULL"),
        ('handover_id', 'INTEGER NULL'),
        ('phase', 'INTEGER NULL'),
        ('parent_run_id', 'INTEGER NULL'),
        ('recheck_context', 'JSON NULL'),
    ],
}


def _add_missing_columns():
    inspector = inspect(engine)
    if 'analysis_runs' not in inspector.get_table_names():
        return
    existing = {column['name'] for column in inspector.get_columns('analysis_runs')}
    with engine.begin() as connection:
        for name, ddl in ADDED_COLUMNS['analysis_runs']:
            if name not in existing:
                connection.execute(text(f'ALTER TABLE analysis_runs ADD COLUMN {name} {ddl}'))


def bootstrap():
    Base.metadata.create_all(engine)
    _add_missing_columns()
