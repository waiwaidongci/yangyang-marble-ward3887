import json
import logging

from sqlalchemy import select, update

from src.config.database import Session
from src.constants.workflow import RunStatus
from src.domain.project import analyze
from src.domain.recheck import analyze_recheck
from src.models.dataset import Dataset
from src.models.run import Run
from src.services.audit import record


def recover_jobs():
    with Session.begin() as session:
        session.execute(update(Run).where(Run.status == RunStatus.RUNNING).values(status=RunStatus.QUEUED))


def claim():
    with Session.begin() as session:
        run = session.scalar(select(Run).where(Run.status == RunStatus.QUEUED)
                             .order_by(Run.id).limit(1).with_for_update(skip_locked=True))
        if run is None:
            return None
        run.status = RunStatus.RUNNING
        dataset = session.get(Dataset, run.dataset_id)
        record(session, 'run.started', run.id)
        if run.kind == 'recheck':
            return {'id': run.id, 'kind': 'recheck', 'rows': dataset.rows,
                    'settings': run.settings, 'context': run.recheck_context}
        return {'id': run.id, 'kind': 'initial', 'rows': dataset.rows,
                'settings': run.settings}


def execute(job):
    run_id = job['id']
    result = None
    error_message = None
    try:
        if job['kind'] == 'recheck':
            context = job['context']
            result = analyze_recheck(
                job['rows'], job['settings'], set(context['sealed_piece_ids']),
                context['scaling_rows'], context['remaining_kits'],
                phase=context['phase'])
        else:
            result = analyze(job['rows'], job['settings'])
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except ValueError as error:
        result = None
        error_message = str(error)
    except Exception:
        logging.getLogger(__name__).exception('计算运行 %s 失败', run_id)
        result = None
        error_message = '计算遇到内部错误，请检查台账后重试。'
    with Session.begin() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.FAILED if error_message is not None else RunStatus.COMPLETED
        run.result = result
        run.error = error_message
        record(session, 'run.' + run.status, run.id)
