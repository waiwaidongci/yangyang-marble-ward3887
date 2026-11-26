"""服务层端到端测试：在轻量替身上跑通封袋/复测/版本切换/并发序列。

先导入 tests.support 注入 FastAPI/SQLAlchemy/pydantic 替身，随后导入的全部是
项目真实服务代码。运行：

    cd backend && PYTHONPATH=. python3 -m unittest tests.test_workflow_fake_orm -v
"""

import tests.support  # noqa: F401  必须在任何 src.* 导入之前

import copy
import csv
import io
import unittest

from fastapi import HTTPException

from src.domain.fixtures import demo_rows
from src.domain.handover import base_ready_groups, recheck_ready_groups
from src.domain.project import analyze
from src.domain.recheck import analyze_recheck
from src.domain.settings import parse_settings
from src.models.bag import SealedBag
from src.models.handover import Handover
from src.models.review import Review
from src.models.run import Run
from src.schemas.handovers import HandoverCreate, RecheckCreate, SealCreate
from src.services import handovers, runs, sealing
from src.services.reviews import submit as review_submit


class Dataset:
    def __init__(self, rows):
        self.id = 1
        self.rows = rows


class FakeSelectResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """只实现项目服务实际调用的 SQLAlchemy Session 接口。"""

    def __init__(self, store):
        self.store = store

    def get(self, model, key):
        if model is Review:
            return self.store.get(Review, {}).get(key)
        return self.store.get(model, {}).get(key)

    def add(self, obj):
        table = self.store.setdefault(type(obj), {})
        if isinstance(obj, Review):
            # Review 以 run_id 为主键。
            table[obj.run_id] = obj
            return
        if getattr(obj, 'id', None) is None or not isinstance(obj.id, int):
            obj.id = max((key for key in table if isinstance(key, int)), default=0) + 1
        table[obj.id] = obj

    def flush(self):
        pass

    def commit(self):
        pass

    def scalar(self, statement):
        rows = self._rows(statement)
        return rows[0] if rows else None

    def scalars(self, statement):
        return FakeSelectResult(self._rows(statement))

    def query(self, model):
        return FakeQuery(list(self.store.get(model, {}).values()))

    def _rows(self, statement):
        model = getattr(statement, 'model', None)
        if model is None:
            return []
        rows = list(self.store.get(model, {}).values())
        for column, expected in getattr(statement, 'filters', []):
            attr = getattr(column, 'test_attr', None)
            if attr is not None:
                rows = [row for row in rows if getattr(row, attr, None) == expected]
        return rows


class FakeQuery:
    """模拟带过滤的查询。

    stub 的 Mapped 列在 == 时返回 (列占位类, 期望值)，没有属性名；项目中唯一
    使用 query().filter(...) 的位置是复测阶段计数，条件固定为 handover_id、kind。
    """

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *conditions):
        rows = self._rows
        for column, expected in conditions:
            attr = getattr(column, 'test_attr', None)
            if attr is not None:
                rows = [row for row in rows if getattr(row, attr, None) == expected]
        return FakeQuery(rows)

    def count(self):
        return len(self._rows)


ADMIN = types = type('Actor', (), {})  # 占位，下面重新定义


class Actor:
    def __init__(self, actor_id, role):
        self.id = actor_id
        self.role = role


ADMIN = Actor(1, 'admin')
ANALYST = Actor(2, 'analyst')


def to_csv(rows):
    buffer = io.StringIO()
    columns = ['id', 'lot_id', 'split', 'long_mm', 'short_mm', 'tone_lstar']
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.settings = parse_settings({})
        self.base_result = analyze(demo_rows(), {})
        self.dataset = Dataset(demo_rows())
        self.store = {
            Run: {1: Run(id=1, label='初版', dataset_id=1, created_by=ANALYST.id,
                         created_at='t0', status='completed', settings=self.settings,
                         result=self.base_result, error=None, kind=None,
                         handover_id=None, phase=None, parent_run_id=None,
                         recheck_context=None)},
            Review: {1: Review(run_id=1, reviewer_id=ADMIN.id, decision='approved',
                               note='核对通过', created_at='t1')},
            Handover: {}, SealedBag: {}, Dataset: {1: self.dataset},
        }
        handovers.Dataset = Dataset

    def session(self):
        return FakeSession(self.store)

    def approve(self, session, run_id):
        payload = type('Payload', (), {'decision': 'approved', 'note': '复测核对通过'})()
        return review_submit(session, run_id, payload, ADMIN)

    def test_full_handover_recheck_switch_cycle(self):
        session = self.session()
        info = handovers.create_handover(session, HandoverCreate(base_run_id=1, max_kits=3), ADMIN)
        handover_id = info['id']
        base_groups = [g['group_id'] for g in base_ready_groups(self.base_result)]

        # 1) 封第一个袋。
        sealed = sealing.seal_bag(session, SealCreate(handover_id=handover_id,
                                                      group_id=base_groups[0], note=''), ADMIN)
        self.assertEqual(sealed['piece_count'], 7)
        detail = handovers.get_detail(session, handover_id, ADMIN)
        self.assertEqual((detail['sealed_kit_count'], detail['remaining_kits']), (1, 2))

        # 2) 同组重复封袋 -> 成员已占用，先确认者为准。
        with self.assertRaises(HTTPException) as ctx:
            sealing.seal_bag(session, SealCreate(handover_id=handover_id,
                                                 group_id=base_groups[0], note=''), ADMIN)
        self.assertEqual(ctx.exception.status_code, 409)

        # 3) 提交复测：范围必须恰好覆盖未封袋件。
        sealed_pieces = set(detail['sealed_piece_ids'])
        unsealed_rows = [r for r in demo_rows() if r['id'] not in sealed_pieces]
        queued = handovers.submit_recheck(
            session, RecheckCreate(handover_id=handover_id, label='第一次复测',
                                   csv=to_csv(unsealed_rows)), ANALYST)
        self.assertEqual(queued['remaining_kits'], 2)
        run = session.get(Run, queued['id'])
        self.assertEqual((run.kind, run.phase), ('recheck', 1))
        self.assertEqual(set(run.recheck_context['sealed_piece_ids']), sealed_pieces)

        # 4) worker 侧领域计算（与 execution.execute 同一入口函数）。
        scaling = next(t for t in self.base_result['tables'] if t['key'] == 'scaling')['rows']
        result = analyze_recheck(copy.deepcopy(unsealed_rows), self.settings,
                                 sealed_pieces, scaling, 2, 1)
        result.pop('_eligible_nodes', None)
        run.status, run.result = 'completed', result

        # 5) 复核通过 -> 一次性切换，旧未封袋组按新分组判定过期。
        review = self.approve(session, run.id)
        self.assertEqual(session.get(Handover, handover_id).active_run_id, run.id)
        self.assertEqual(session.get(Handover, handover_id).switch_log[-1]['phase'], 1)
        self.assertIn('switch', review)

        # 6) 旧版本分组不能再封袋（过期分组）。
        with self.assertRaises(HTTPException) as ctx:
            sealing.seal_bag(session, SealCreate(handover_id=handover_id,
                                                 group_id=base_groups[1], note=''), ADMIN)
        self.assertEqual(ctx.exception.status_code, 404)

        # 7) 新版本套件可以继续封袋，且挂在新运行下。
        new_groups = [g['group_id'] for g in recheck_ready_groups(result, 1)]
        self.assertTrue(new_groups)
        sealed2 = sealing.seal_bag(session, SealCreate(handover_id=handover_id,
                                                       group_id=new_groups[0], note=''), ADMIN)
        self.assertEqual(sealed2['run_id'], run.id)

        # 8) 生效复测版本可下载正式 CSV。
        table = runs.export_table(session, run.id, 'kit_selection', ADMIN)
        self.assertEqual(table['key'], 'kit_selection')

    def test_superseded_recheck_cannot_be_downloaded(self):
        session = self.session()
        info = handovers.create_handover(session, HandoverCreate(base_run_id=1), ADMIN)
        handover_id = info['id']
        scaling = next(t for t in self.base_result['tables'] if t['key'] == 'scaling')['rows']

        def run_phase(phase, remaining):
            queued = handovers.submit_recheck(
                session, RecheckCreate(handover_id=handover_id, label=f'复测{phase}',
                                       csv=to_csv(copy.deepcopy(demo_rows()))), ANALYST)
            run = session.get(Run, queued['id'])
            result = analyze_recheck(copy.deepcopy(demo_rows()), self.settings,
                                     set(), scaling, remaining, phase)
            result.pop('_eligible_nodes', None)
            run.status, run.result = 'completed', result
            return run

        first = run_phase(1, 3)
        self.approve(session, first.id)
        second = run_phase(2, 3)
        self.approve(session, second.id)

        # 第一版已被第二版取代：下载被拒；第二版放行。
        with self.assertRaises(HTTPException) as ctx:
            runs.export_table(session, first.id, 'recheck_members', ADMIN)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(runs.export_table(session, second.id, 'recheck_members', ADMIN)['key'],
                         'recheck_members')

    def test_recheck_scope_rejection(self):
        session = self.session()
        info = handovers.create_handover(session, HandoverCreate(base_run_id=1), ADMIN)
        with self.assertRaises(ValueError):
            handovers.submit_recheck(
                session, RecheckCreate(handover_id=info['id'], label='残缺复测',
                                       csv=to_csv(demo_rows()[:-1])), ANALYST)

    def test_quota_cannot_be_exceeded_and_blocks_further_recheck(self):
        session = self.session()
        info = handovers.create_handover(session, HandoverCreate(base_run_id=1, max_kits=1), ADMIN)
        groups = [g['group_id'] for g in base_ready_groups(self.base_result)]
        sealing.seal_bag(session, SealCreate(handover_id=info['id'], group_id=groups[0], note=''), ADMIN)
        with self.assertRaises(HTTPException) as ctx:
            sealing.seal_bag(session, SealCreate(handover_id=info['id'],
                                                 group_id=groups[1], note=''), ADMIN)
        self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            handovers.submit_recheck(
                session, RecheckCreate(handover_id=info['id'], label='复测',
                                       csv=to_csv(copy.deepcopy(demo_rows()))), ANALYST)

    def test_sealed_pieces_are_excluded_from_recheck_tree(self):
        session = self.session()
        info = handovers.create_handover(session, HandoverCreate(base_run_id=1, max_kits=3), ADMIN)
        groups = [g['group_id'] for g in base_ready_groups(self.base_result)]
        sealing.seal_bag(session, SealCreate(handover_id=info['id'], group_id=groups[0], note=''), ADMIN)
        detail = handovers.get_detail(session, info['id'], ADMIN)
        sealed_pieces = set(detail['sealed_piece_ids'])
        unsealed_rows = [r for r in demo_rows() if r['id'] not in sealed_pieces]
        queued = handovers.submit_recheck(
            session, RecheckCreate(handover_id=info['id'], label='复测',
                                   csv=to_csv(unsealed_rows)), ANALYST)
        run = session.get(Run, queued['id'])
        scaling = next(t for t in self.base_result['tables'] if t['key'] == 'scaling')['rows']
        result = analyze_recheck(copy.deepcopy(unsealed_rows), self.settings,
                                 sealed_pieces, scaling, 2, 1)
        result.pop('_eligible_nodes', None)
        run.status, run.result = 'completed', result
        members = next(t for t in result['tables'] if t['key'] == 'recheck_members')
        seen = {row['id'] for row in members['rows']}
        self.assertFalse(seen & sealed_pieces)
        snapshot = next(t for t in result['tables'] if t['key'] == 'sealed_snapshot')
        self.assertEqual({r['piece_id'] for r in snapshot['rows']}, sealed_pieces)


if __name__ == '__main__':
    unittest.main()
