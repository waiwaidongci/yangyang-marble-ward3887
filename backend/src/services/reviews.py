from fastapi import HTTPException
from sqlalchemy import select

from src.constants.workflow import RunStatus
from src.models.review import Review
from src.models.run import Run
from src.services.audit import record
from src.services.handovers import apply_approved_switch
from src.utils.time import utc_stamp


def review_info(review):
    return {'run_id': review.run_id, 'decision': review.decision, 'note': review.note, 'created_at': review.created_at}


def submit(session, run_id, payload, actor):
    run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run:
        raise LookupError('找不到运行')
    if run.status != RunStatus.COMPLETED or session.get(Review, run_id):
        raise HTTPException(409, '仅可复核一次，且计算必须成功完成')
    review = Review(run_id=run_id, reviewer_id=actor.id, decision=payload.decision,
                    note=payload.note, created_at=utc_stamp())
    session.add(review)
    switch_outcome = None
    if payload.decision == 'approved':
        # 复测版本在复核通过的同一事务内一次性切换；失败则整笔签核回滚，
        # 过期计算不会成为生效版本。
        switch_outcome = apply_approved_switch(session, run, actor)
    record(session, 'review.' + payload.decision, run_id, actor.id)
    session.commit()
    response = review_info(review)
    if switch_outcome is not None:
        response['switch'] = {
            'stale_group_ids': switch_outcome['stale_group_ids'],
            'effective_group_ids': [group['group_id'] for group in switch_outcome['effective_groups']],
            'quota_remaining_after_switch': switch_outcome['quota_remaining_after_switch'],
        }
    return response
