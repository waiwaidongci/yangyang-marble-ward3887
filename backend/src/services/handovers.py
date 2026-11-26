"""交接批次、复测提交与版本切换的事务服务。"""

from fastapi import HTTPException
from sqlalchemy import select

from src.constants.workflow import Decision, RunStatus
from src.domain.handover import (
    base_ready_groups, evaluate_switch, recheck_ready_groups, validate_recheck_scope,
)
from src.domain.project import SPEC, normalize_rows
from src.models.dataset import Dataset
from src.models.handover import Handover
from src.models.review import Review
from src.models.run import Run
from src.services.audit import record
from src.services.queue import notify_worker
from src.utils.csv_files import read_rows
from src.utils.time import utc_stamp

MAX_HANDOVER_KITS = 60


def _load(session, handover_id: int) -> Handover:
    handover = session.get(Handover, handover_id)
    if handover is None:
        raise LookupError('找不到交接批次')
    return handover


def create_handover(session, payload, actor):
    run = session.get(Run, payload.base_run_id)
    review = session.get(Review, payload.base_run_id)
    if run is None or review is None or review.decision != Decision.APPROVED:
        raise HTTPException(409, '只有复核通过的运行才能开立交接批次')
    if run.kind == 'recheck':
        raise HTTPException(409, '复测运行用于版本切换，不能再开立交接批次')
    if session.scalar(select(Handover).where(Handover.base_run_id == run.id)):
        raise HTTPException(409, '该运行已开立交接批次')
    groups = base_ready_groups(run.result)
    if not groups:
        raise HTTPException(409, '建议切法下没有可交接套件，无法封袋')
    max_kits = payload.max_kits if payload.max_kits is not None else len(groups)
    if not 1 <= max_kits <= MAX_HANDOVER_KITS:
        raise HTTPException(422, f'套数上限须在 1 至 {MAX_HANDOVER_KITS} 之间')
    handover = Handover(base_run_id=run.id, dataset_id=run.dataset_id, created_by=actor.id,
                        created_at=utc_stamp(), max_kits=max_kits,
                        active_run_id=run.id, switch_log=[])
    session.add(handover)
    session.flush()
    record(session, 'handover.opened', handover.id, actor.id)
    session.commit()
    return handover_info(session, handover)


def _sealed_bags(session, handover_id: int):
    from src.models.bag import SealedBag
    return list(session.scalars(select(SealedBag).where(
        SealedBag.handover_id == handover_id).order_by(SealedBag.id)))


def handover_info(session, handover: Handover) -> dict:
    bags = _sealed_bags(session, handover.id)
    sealed_pieces = sorted({piece for bag in bags for piece in bag.piece_ids})
    return {
        'id': handover.id, 'base_run_id': handover.base_run_id,
        'dataset_id': handover.dataset_id, 'created_at': handover.created_at,
        'max_kits': handover.max_kits, 'active_run_id': handover.active_run_id,
        'sealed_kit_count': len(bags), 'sealed_piece_count': len(sealed_pieces),
        'remaining_kits': handover.max_kits - len(bags),
        'sealed_piece_ids': sealed_pieces, 'switch_log': handover.switch_log,
    }


def get_detail(session, handover_id: int, actor):
    return handover_info(session, _load(session, handover_id))


def list_handovers(session, actor):
    return [handover_info(session, handover)
            for handover in session.scalars(select(Handover).order_by(Handover.id.desc()))]


def submit_recheck(session, payload, actor):
    # 交接行加锁，使“复测提交”与“封袋确认”在同一串行序列上先确认者为准。
    handover = session.scalar(select(Handover).where(
        Handover.id == payload.handover_id).with_for_update())
    if handover is None:
        raise LookupError('找不到交接批次')
    bags = _sealed_bags(session, handover.id)
    sealed_pieces = {piece for bag in bags for piece in bag.piece_ids}
    remaining = handover.max_kits - len(bags)
    if remaining <= 0:
        raise HTTPException(409, '套数上限已被已封袋套件占满，无需再复测')
    base_dataset = session.get(Dataset, handover.dataset_id)
    base_run = session.get(Run, handover.base_run_id)
    scaling = next(table for table in base_run.result['tables']
                   if table['key'] == 'scaling')
    rows = normalize_rows(read_rows(payload.csv, SPEC['columns']))
    validate_recheck_scope(base_dataset.rows, sealed_pieces, rows)
    phase = session.query(Run).filter(Run.handover_id == handover.id,
                                      Run.kind == 'recheck').count() + 1
    run = Run(label=payload.label, dataset_id=handover.dataset_id, created_by=actor.id,
              created_at=utc_stamp(), status=RunStatus.QUEUED,
              settings=base_run.settings,
              kind='recheck', handover_id=handover.id, phase=phase,
              parent_run_id=handover.active_run_id,
              recheck_context={'sealed_piece_ids': sorted(sealed_pieces),
                               'remaining_kits': remaining,
                               'scaling_rows': scaling['rows'],
                               'phase': phase})
    session.add(run)
    session.flush()
    record(session, 'recheck.queued', run.id, actor.id)
    session.commit()
    notify_worker()
    return {'id': run.id, 'phase': phase, 'handover_id': handover.id,
            'remaining_kits': remaining, 'status': run.status}


def apply_approved_switch(session, run: Run, actor) -> dict | None:
    """复测运行复核通过时调用；普通运行返回 None。"""
    if run.kind != 'recheck':
        return None
    handover = session.scalar(select(Handover).where(
        Handover.id == run.handover_id).with_for_update())
    bags = _sealed_bags(session, handover.id)
    sealed_pieces = {piece for bag in bags for piece in bag.piece_ids}
    active_run = session.get(Run, handover.active_run_id)
    if active_run.kind == 'recheck':
        active_groups = recheck_ready_groups(active_run.result, active_run.phase)
    else:
        active_groups = base_ready_groups(active_run.result)
    candidate_groups = recheck_ready_groups(run.result, run.phase)
    outcome = evaluate_switch(active_groups, sealed_pieces, candidate_groups,
                              handover.max_kits, len(bags))
    if outcome['quota_remaining_after_switch'] < 0:
        raise HTTPException(409, '切换后套件数将超过套数上限，本次复测不能生效')
    handover.active_run_id = run.id
    handover.switch_log = list(handover.switch_log or []) + [{
        'phase': run.phase, 'run_id': run.id, 'switched_at': utc_stamp(),
        'stale_group_ids': outcome['stale_group_ids'],
        'effective_group_ids': [group['group_id'] for group in outcome['effective_groups']],
        'sealed_kit_count': len(bags), 'remaining_kits': outcome['quota_remaining_after_switch'],
        'reviewer_id': actor.id if actor else None,
    }]
    record(session, 'handover.switched', handover.id, actor.id if actor else None)
    return outcome
