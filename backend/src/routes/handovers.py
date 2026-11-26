from fastapi import APIRouter

from src.controllers.dependencies import Actor, Db, Writer
from src.schemas.handovers import (
    HandoverCreate, HandoverInfo, RecheckCreate, RecheckInfo,
    SealCreate, SealedBagInfo,
)
from src.services import handovers, sealing

router = APIRouter(prefix='/handovers')


@router.post('', response_model=HandoverInfo, status_code=201)
def open_handover(payload: HandoverCreate, session: Db, actor: Writer):
    return handovers.create_handover(session, payload, actor)


@router.get('', response_model=list[HandoverInfo])
def list_all(session: Db, actor: Actor):
    return handovers.list_handovers(session, actor)


@router.get('/{handover_id}', response_model=HandoverInfo)
def detail(handover_id: int, session: Db, actor: Actor):
    return handovers.get_detail(session, handover_id, actor)


@router.post('/{handover_id}/rechecks', response_model=RecheckInfo, status_code=202)
def recheck(handover_id: int, payload: RecheckCreate, session: Db, actor: Writer):
    if payload.handover_id != handover_id:
        from fastapi import HTTPException
        raise HTTPException(422, '路径与请求体中的交接批次不一致')
    return handovers.submit_recheck(session, payload, actor)


@router.post('/{handover_id}/seals', status_code=201)
def seal(handover_id: int, payload: SealCreate, session: Db, actor: Writer):
    if payload.handover_id != handover_id:
        from fastapi import HTTPException
        raise HTTPException(422, '路径与请求体中的交接批次不一致')
    return sealing.seal_bag(session, payload, actor)


@router.get('/{handover_id}/seals', response_model=list[SealedBagInfo])
def seals(handover_id: int, session: Db, actor: Actor):
    return sealing.list_bags(session, handover_id, actor)
