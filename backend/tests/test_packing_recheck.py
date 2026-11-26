"""纯领域规则测试：跨深度选套、冻结标尺复测、版本切换与过期分组。

只用 Python 标准库运行：
    cd backend && PYTHONPATH=. python3 -m unittest discover -s tests -v
"""

import copy
import unittest

from src.domain.fixtures import demo_rows
from src.domain.handover import (
    assert_group_sealable, base_ready_groups, evaluate_switch,
    recheck_ready_groups, validate_recheck_scope,
)
from src.domain.packing import select_kits
from src.domain.project import analyze
from src.domain.recheck import analyze_recheck, restore_scales
from src.domain.settings import parse_settings
from src.domain.ward import build_tree
from src.domain.scaling import fit_scales


def tree_from_rows(eligible, scales):
    points = {row['id']: tuple(scale.apply(row) for scale in scales) for row in eligible}
    return build_tree(points)


class PackingTests(unittest.TestCase):
    def setUp(self):
        self.options = parse_settings({})
        self.base = analyze(demo_rows(), {})
        scaling = next(t for t in self.base['tables'] if t['key'] == 'scaling')
        self.scales = restore_scales(scaling['rows'])

    def test_demo_packing_prefers_more_pieces_within_quota(self):
        # 演示台账：5 簇深度下 3 套 21 件；名额 3 时跨深度至少不劣于该结果。
        eligible = [r for r in demo_rows() if r['split'] == 'pending'
                    and r['long_mm'] >= 85 and r['short_mm'] >= 55]
        tree = tree_from_rows(eligible, self.scales)
        kits = select_kits(tree, self.options, 3)
        self.assertLessEqual(len(kits), 3)
        self.assertEqual(sum(k['group_pieces'] for k in kits), 21)
        nodes = [k['node'] for k in kits]
        self.assertEqual(len(nodes), len(set(nodes)))

    def test_kits_are_member_disjoint_across_depths(self):
        eligible = [r for r in demo_rows() if r['split'] == 'pending'
                    and r['long_mm'] >= 85 and r['short_mm'] >= 55]
        tree = tree_from_rows(eligible, self.scales)
        kits = select_kits(tree, self.options, 4)
        seen = set()
        for kit in kits:
            self.assertFalse(seen.intersection(kit['members']))
            seen.update(kit['members'])
        self.assertLessEqual(len(kits), 4)

    def test_quota_zero_returns_empty(self):
        eligible = [r for r in demo_rows() if r['split'] == 'pending'
                    and r['long_mm'] >= 85 and r['short_mm'] >= 55]
        tree = tree_from_rows(eligible, self.scales)
        self.assertEqual(select_kits(tree, self.options, 0), [])

    def test_tie_break_by_sse_then_member_ids(self):
        # 构造 4 个互相远离、每簇恰好 4 件的等距小组件：名额 1 时应选 SSE 最小
        # （单点簇 SSE 为 0，任何 2 件以上组合 SSE 更大）且编号最小的组。
        rows = []
        centers = ((100, 60, 80), (160, 100, 60), (200, 130, 40), (120, 80, 50))
        for ci, (length, width, tone) in enumerate(centers, 1):
            for pi in range(4):
                rows.append({'id': f'T{ci}-{pi}', 'lot_id': f'L{ci}', 'split': 'pending',
                             'long_mm': float(length), 'short_mm': float(width),
                             'tone_lstar': float(tone)})
        scales = fit_scales(rows, 1.25)
        tree = tree_from_rows(rows, scales)
        kits = select_kits(tree, self.options, 1)
        self.assertEqual(len(kits), 1)
        chosen = kits[0]
        self.assertEqual(chosen['group_pieces'], 4)
        self.assertEqual(chosen['group_sse'], 0.0)
        self.assertEqual(chosen['members'][0], 'T1-0')

    def test_small_clusters_never_become_kits(self):
        # 放宽距离门槛后，件数不足的组仍然不能整体入选。
        options = parse_settings({'max_ward_distance': 100.0})
        eligible = [r for r in demo_rows() if r['split'] == 'pending'
                    and r['long_mm'] >= 85 and r['short_mm'] >= 55]
        tree = tree_from_rows(eligible, self.scales)
        kits = select_kits(tree, options, 6)
        for kit in kits:
            self.assertGreaterEqual(kit['group_pieces'], 4)
            self.assertLessEqual(kit['group_ward_distance'], 100.0)


class RecheckTests(unittest.TestCase):
    def setUp(self):
        self.rows = demo_rows()
        self.base = analyze(self.rows, {})
        self.scaling_rows = next(t for t in self.base['tables'] if t['key'] == 'scaling')['rows']
        self.settings = parse_settings({})

    def test_recheck_rebuilds_with_latest_readings(self):
        sealed = {'NOV25-MIX-01'}  # 模拟封掉一件（它在演示中本就暂缓，此处只验流程）
        recheck_rows = [copy.deepcopy(r) for r in self.rows if r['id'] not in sealed]
        # 给若干未封袋件更新读数（明度整体微调），应重建出新树而标尺不变。
        for row in recheck_rows:
            if row['split'] == 'pending':
                row['tone_lstar'] = round(min(100.0, row['tone_lstar'] + 1.0), 2)
        result = analyze_recheck(recheck_rows, self.settings, sealed, self.scaling_rows, 3, 1)
        self.assertEqual(result['metrics']['已封袋件数'], 1)
        keys = {t['key'] for t in result['tables']}
        self.assertEqual(keys, {'kit_selection', 'recheck_members', 'ward_merges',
                                'scaling', 'set_aside', 'sealed_snapshot'})
        snapshot = next(t for t in result['tables'] if t['key'] == 'sealed_snapshot')
        self.assertEqual([r['piece_id'] for r in snapshot['rows']], ['NOV25-MIX-01'])
        scaling = next(t for t in result['tables'] if t['key'] == 'scaling')
        self.assertEqual(scaling['rows'][0]['reference_mean'], self.scaling_rows[0]['reference_mean'])

    def test_recheck_scope_must_cover_every_unsealed_piece(self):
        sealed = set()
        good = [copy.deepcopy(r) for r in self.rows]
        validate_recheck_scope(self.rows, sealed, good)  # 完整覆盖通过
        with self.assertRaises(ValueError):
            validate_recheck_scope(self.rows, sealed, good[:-1])
        with self.assertRaises(ValueError):
            extra = good + [{'id': 'NOV99-X-01', 'lot_id': 'NOV99-X', 'split': 'pending',
                             'long_mm': 100.0, 'short_mm': 60.0, 'tone_lstar': 70.0}]
            validate_recheck_scope(self.rows, sealed, extra)
        with self.assertRaises(ValueError):
            validate_recheck_scope(self.rows, {'NOV25-01-01'}, good)

    def test_recheck_respects_remaining_quota(self):
        sealed = set()
        result = analyze_recheck(copy.deepcopy(self.rows), self.settings, sealed,
                                 self.scaling_rows, 1, 2)
        kits = next(t for t in result['tables'] if t['key'] == 'kit_selection')['rows']
        self.assertLessEqual(len(kits), 1)
        self.assertEqual(result['metrics']['剩余套数名额'], 1)


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.base = analyze(demo_rows(), {})
        self.groups = base_ready_groups(self.base)
        self.assertTrue(self.groups)

    def test_base_groups_extracted_from_approved_run(self):
        ids = [g['group_id'] for g in self.groups]
        self.assertEqual(len(ids), len(set(ids)))
        for group in self.groups:
            self.assertGreaterEqual(len(group['pieces']), 4)

    def test_switch_marks_overlapped_old_groups_stale(self):
        # 候选新版本故意只保留第一组的成员集合，其余旧组应全部过期。
        first = self.groups[0]
        candidate = [{'group_id': 'S01-K01', 'node': 99, 'pieces': set(first['pieces']), 'phase': 1}]
        outcome = evaluate_switch(self.groups, set(), candidate, max_kits=3, sealed_kit_count=0)
        self.assertEqual(outcome['stale_group_ids'], [g['group_id'] for g in self.groups[1:]])
        self.assertEqual(outcome['quota_remaining_after_switch'], 2)

    def test_sealed_old_group_never_marked_stale(self):
        sealed_group = self.groups[0]
        sealed_pieces = set(sealed_group['pieces'])
        candidate = [{'group_id': 'S01-K01', 'node': 99,
                      'pieces': set(self.groups[1]['pieces']), 'phase': 1}]
        outcome = evaluate_switch(self.groups, sealed_pieces, candidate, 3, 1)
        self.assertNotIn(sealed_group['group_id'], outcome['stale_group_ids'])
        # 新候选与已封袋成员互不重叠才有效；这里没有重叠。
        self.assertEqual(len(outcome['effective_groups']), 1)

    def test_candidate_overlapping_sealed_pieces_is_dropped(self):
        sealed_pieces = set(self.groups[0]['pieces'])
        candidate = [{'group_id': 'S01-K01', 'node': 99,
                      'pieces': sealed_pieces | {'NOV25-MIX-02'}, 'phase': 1}]
        outcome = evaluate_switch(self.groups, sealed_pieces, candidate, 3, 1)
        self.assertEqual(outcome['effective_groups'], [])

    def test_stale_group_cannot_be_sealed(self):
        group = {'group_id': 'K05-G01', 'pieces': {'a', 'b'}, 'node': 1}
        with self.assertRaises(ValueError):
            assert_group_sealable(group, set(), current_run_id=2, group_run_id=1)
        with self.assertRaises(ValueError):
            assert_group_sealable(group, {'a'}, current_run_id=1, group_run_id=1)
        # 同版本且成员未占用时通过。
        assert_group_sealable(group, set(), current_run_id=1, group_run_id=1)

    def test_recheck_group_roundtrip(self):
        sealed = set()
        result = analyze_recheck(copy.deepcopy(demo_rows()), parse_settings({}), sealed,
                                 next(t for t in self.base['tables']
                                      if t['key'] == 'scaling')['rows'], 3, 1)
        groups = recheck_ready_groups(result, 1)
        self.assertTrue(groups)
        for group in groups:
            self.assertEqual(group['phase'], 1)
            self.assertGreaterEqual(len(group['pieces']), 4)


if __name__ == '__main__':
    unittest.main()
