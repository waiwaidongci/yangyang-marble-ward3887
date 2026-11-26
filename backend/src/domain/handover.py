"""封袋/复测并发的版本规则（纯领域逻辑，不接触数据库）。

* 封袋与复测都在交接批次上串行确认，先确认者为准；
* 复测复核通过时一次性把生效版本切到新运行；
* 切换后被新分组打散的旧版本分组立即过期，不能再封袋；
* 已封袋件原样交接并持续占用套数上限。
"""

from .recheck import check_recheck_coverage


def base_ready_groups(run_result: dict) -> list[dict]:
    """从初版运行提取建议切法（recommended）下的 ready 套件。

    kit_members 同时列出 3–6 簇各候选深度的分组，只有建议切法内的套件成员
    互不重叠，可作为交接批次的初始封袋分组。
    """
    cut_table = next((item for item in run_result['tables']
                      if item['key'] == 'cut_candidates'), None)
    recommended = next((row['clusters'] for row in cut_table['rows']
                        if row['recommended']), None) if cut_table else None
    table = next((item for item in run_result['tables'] if item['key'] == 'kit_members'), None)
    groups: dict[str, dict] = {}
    if table is None or recommended is None:
        return []
    for row in table['rows']:
        if row['kit_status'] != 'ready' or row['clusters'] != recommended:
            continue
        group = groups.setdefault(row['group_id'], {'group_id': row['group_id'], 'node': row['node'],
                                                     'pieces': set(), 'phase': 0})
        group['pieces'].add(row['id'])
    return [groups[key] for key in sorted(groups)]


def recheck_ready_groups(run_result: dict, phase: int) -> list[dict]:
    """从复测运行的 kit_selection + recheck_members 表提取入选套件。"""
    selection = next((item for item in run_result['tables'] if item['key'] == 'kit_selection'), None)
    members = next((item for item in run_result['tables'] if item['key'] == 'recheck_members'), None)
    if selection is None or members is None:
        return []
    pieces_by_group: dict[str, set[str]] = {}
    for row in members['rows']:
        if row['kit_status'] == 'ready' and row['group_id']:
            pieces_by_group.setdefault(row['group_id'], set()).add(row['id'])
    groups = []
    for row in selection['rows']:
        if row['kit_status'] != 'ready':
            continue
        groups.append({'group_id': row['group_id'], 'node': row['node'],
                       'pieces': set(pieces_by_group.get(row['group_id'], set())), 'phase': phase})
    return sorted(groups, key=lambda group: group['group_id'])


def evaluate_switch(active_groups: list[dict], sealed_pieces: set[str],
                    candidate_groups: list[dict], max_kits: int, sealed_kit_count: int) -> dict:
    """计算切换到候选版本的影响，返回过期旧组与切换后仍可封袋的新组。"""
    stale_groups = []
    for group in active_groups:
        if group['pieces'] & sealed_pieces:
            continue  # 已封袋组原样交接，不参与版本比较。
        if not any(group['pieces'] <= candidate['pieces'] for candidate in candidate_groups):
            stale_groups.append(group['group_id'])
    effective = [group for group in candidate_groups if not (group['pieces'] & sealed_pieces)]
    remaining = max_kits - sealed_kit_count - len(effective)
    return {'stale_group_ids': stale_groups, 'effective_groups': effective,
            'quota_remaining_after_switch': remaining}


def validate_recheck_scope(base_rows: list[dict], sealed_ids: set[str], recheck_rows: list[dict]) -> None:
    """提交复测前校验台账范围：恰好覆盖全部未封袋的待分拣件。"""
    check_recheck_coverage(base_rows, sealed_ids, recheck_rows)


def assert_group_sealable(group: dict, sealed_pieces: set[str], current_run_id: int,
                          group_run_id: int) -> None:
    """封袋确认时的最终防线：过期组或已被占用的成员一律拒绝。"""
    if group_run_id != current_run_id:
        raise ValueError('该分组来自已过期的计算版本，不能封袋。')
    overlap = group['pieces'] & sealed_pieces
    if overlap:
        raise ValueError('套件成员已封袋或已被其他套件占用：' + '、'.join(sorted(overlap)) + '。')
