"""封袋确认：在交接批次行锁内串行处理，保证先确认者为准。"""

from fastapi import HTTPException
from sqlalchemy import select

from src.domain.handover import (
    assert_group_sealable, base_ready_groups, recheck_ready_groups,
)
from src.models.bag import SealedBag
from src.models.handover import Handover
from src.models.run import Run
from src.services.audit import record
from src.services.handovers import _sealed_bags, handover_info
from src.utils.time import utc_stamp


def _groups_of(run: Run):
    if run.kind == 'recheck':
        return {group['group_id']: group
                for group in recheck_ready_groups(run.result, run.phase)}
    return {group['group_id']: group for group in base_ready_groups(run.result)}


def seal_bag(session, payload, actor):
    handover = session.scalar(select(Handover).where(
        Handover.id == payload.handover_id).with_for_update())
    if handover is None:
        raise LookupError('找不到交接批次')
    bags = _sealed_bags(session, handover.id)
    if len(bags) >= handover.max_kits:
        raise HTTPException(409, '套数上限已占满，不能再封袋')
    active_run = session.get(Run, handover.active_run_id)
    groups = _groups_of(active_run)
    group = groups.get(payload.group_id)
    if group is None:
        raise HTTPException(404, '当前生效版本没有可封袋的该分组（可能已随复测过期）')
    sealed_pieces = {piece for bag in bags for piece in bag.piece_ids}
    try:
        assert_group_sealable(group, sealed_pieces, handover.active_run_id, active_run.id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    bag = SealedBag(handover_id=handover.id, run_id=active_run.id, group_id=group['group_id'],
                    piece_ids=sorted(group['pieces']), piece_count=len(group['pieces']),
                    sealed_by=actor.id, sealed_at=utc_stamp(), note=payload.note or '')
    session.add(bag)
    session.flush()
    record(session, 'bag.sealed', bag.id, actor.id)
    session.commit()
    return {'id': bag.id, 'handover_id': handover.id, 'run_id': bag.run_id,
            'group_id': bag.group_id, 'piece_ids': bag.piece_ids,
            'piece_count': bag.piece_count, 'sealed_at': bag.sealed_at,
            'handover': handover_info(session, handover)}


def list_bags(session, handover_id: int, actor):
    handover = session.get(Handover, handover_id)
    if handover is None:
        raise LookupError('找不到交接批次')
    return [{'id': bag.id, 'handover_id': bag.handover_id, 'run_id': bag.run_id,
             'group_id': bag.group_id, 'piece_ids': bag.piece_ids,
             'piece_count': bag.piece_count, 'sealed_at': bag.sealed_at, 'note': bag.note}
            for bag in _sealed_bags(session, handover_id)]
