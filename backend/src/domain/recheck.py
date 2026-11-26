"""复测分析：沿用父运行冻结的标尺，对未封袋料用最新读数重建 Ward 树。

复测不得重新拟合缩放，也不得改动尺寸、件数和合并距离门槛，保证新旧版本
在同一加权标准化空间内可比。
"""

from dataclasses import dataclass

from .packing import select_kits, selection_sse
from .records import normalize_rows, size_screen
from .reports import recheck_reports
from .settings import FEATURES
from .ward import build_tree


@dataclass(frozen=True)
class FrozenScale:
    feature: str
    center: float
    population_sd: float
    effective_scale: float
    weight: float

    def apply(self, row: dict) -> float:
        return (row[self.feature] - self.center) * self.weight / self.effective_scale


def restore_scales(scaling_rows: list[dict]) -> tuple[FrozenScale, ...]:
    """从父运行 scaling 表逐字段恢复冻结标尺，避免复测时重新拟合。"""
    by_feature = {row['feature']: row for row in scaling_rows}
    scales = []
    for feature in FEATURES:
        if feature not in by_feature:
            raise ValueError('父运行缺少冻结标尺，无法复测。')
        row = by_feature[feature]
        scales.append(FrozenScale(
            feature, float(row['reference_mean']), float(row['reference_population_sd']),
            float(row['effective_scale']), float(row['coordinate_weight'])))
    return tuple(scales)


def check_recheck_coverage(base_rows: list[dict], sealed_ids: set[str], recheck_rows: list[dict]) -> None:
    """复测台账必须恰好覆盖全部未封袋料，一件不能少，已封袋件不得出现。"""
    pending_ids = {row['id'] for row in base_rows if row['split'] == 'pending'}
    unsealed_ids = pending_ids - sealed_ids
    incoming = {row['id'] for row in recheck_rows if row['split'] == 'pending'}
    missing = sorted(unsealed_ids - incoming)
    stale = sorted(sealed_ids & incoming)
    unknown = sorted(incoming - pending_ids)
    if missing or stale or unknown:
        details = []
        if missing:
            details.append('缺少未封袋件 ' + '、'.join(missing))
        if stale:
            details.append('已封袋件不得复测 ' + '、'.join(stale))
        if unknown:
            details.append('编号不属于本交接批次 ' + '、'.join(unknown))
        raise ValueError('复测台账范围无效：' + '；'.join(details) + '。')


def analyze_recheck(rows: list[dict], settings: dict, sealed_ids: set[str],
                    scaling_rows: list[dict], remaining_kits: int, phase: int) -> dict:
    options = settings
    cohorts = {split: [] for split in ('train', 'holdout', 'pending')}
    normalized = normalize_rows(rows)
    for row in normalized:
        cohorts[row['split']].append(row)
    scales = restore_scales(scaling_rows)
    eligible, aside = size_screen(cohorts['pending'], options)
    tree = build_tree({row['id']: tuple(scale.apply(row) for scale in scales) for row in eligible})
    kits = select_kits(tree, options, remaining_kits)
    pieces_ready = sum(kit['group_pieces'] for kit in kits)
    warnings = []
    if not eligible:
        warnings.append('全部未封袋单件低于尺寸门槛，本次复测无可切树数据。')
    elif len(eligible) < options['max_clusters']:
        warnings.append(f'符合尺寸的边角料仅 {len(eligible)} 件，候选簇数上限按实际件数收缩。')
    if eligible and not kits:
        warnings.append('没有套件同时满足套件件数与合并距离门槛，所有未封袋单件暂缓交接。')
    tables = recheck_reports(tree, kits, eligible, aside, scales, options,
                             sealed_ids, remaining_kits, phase)
    return {
        'summary': (f'第 {phase} 版复测：{len(sealed_ids)} 件已封袋原样交接，'
                    f'{len(eligible)} 件未封袋料按最新读数重建 Ward 树，'
                    f'{len(aside)} 件尺寸不足搁置；本版入选 {len(kits)} 套共 {pieces_ready} 件。'),
        'metrics': {
            '复测阶段': phase,
            '已封袋件数': len(sealed_ids),
            '剩余套数名额': remaining_kits,
            '入树件数': len(eligible),
            '尺寸不足件数': len(aside),
            '入选套数': len(kits),
            '入选件数': pieces_ready,
            '入选套件SSE总和': selection_sse(kits),
            '暂缓件数': len(eligible) - pieces_ready,
        },
        'tables': tables,
        'warnings': warnings,
    }
