"""跨深度套件选择：在 Ward 树上用动态规划挑选互不重叠的合格套件。

封袋与复测并发时，复测方案不再限定单一切树深度，而是可以从树的不同深度
挑选成员互不重叠的簇作为套件。选择目标依次为：

1. 在剩余套数名额内让交接件数最多；
2. 件数持平时，取所选套件簇内平方和（SSE）总和最小的；
3. 仍然持平时，按成员编号字典序选择（优先包含更小编号的方案）。
"""

from dataclasses import dataclass
from math import fsum

from .ward import Tree


@dataclass(frozen=True)
class Choice:
    """某个子树内、恰好使用 kit_count 个套件时的最优选择。"""

    kit_count: int
    pieces: int
    sse: float
    nodes: tuple[int, ...]
    signature: tuple[tuple[str, ...], ...]

    def key(self) -> tuple:
        return (-self.pieces, self.sse, self.signature)


def _better(current: Choice | None, challenger: Choice) -> Choice:
    if current is None or challenger.key() < current.key():
        return challenger
    return current


def _merge(left: Choice, right: Choice) -> Choice:
    """合并两个子树的选择：成员天然互不重叠，套件数与指标相加。"""
    return Choice(
        left.kit_count + right.kit_count,
        left.pieces + right.pieces,
        fsum((left.sse, right.sse)),
        tuple(sorted(left.nodes + right.nodes)),
        tuple(sorted(left.signature + right.signature)),
    )


def eligible_nodes(tree: Tree, options: dict) -> set[int]:
    """满足每套最少件数与簇根最大 Ward 距离的节点才能整体作为套件。"""
    qualified = set()
    for index, node in enumerate(tree.nodes):
        if len(node.members) >= options['min_kit_pieces'] and node.height <= options['max_ward_distance']:
            qualified.add(index)
    return qualified


def select_kits(tree: Tree, options: dict, max_kits: int) -> list[dict]:
    """返回按成员编号排序的入选套件列表；无合格套件时返回空列表。"""
    if not tree.nodes or max_kits <= 0:
        return []
    qualified = eligible_nodes(tree, options)
    capacity = min(max_kits, len(tree.nodes))
    # 每个节点保存 {套件数: 最优 Choice}，空选择（0 套、0 件）始终存在。
    plans: list[dict[int, Choice]] = []
    for index, node in enumerate(tree.nodes):
        best: dict[int, Choice] = {0: Choice(0, 0, 0.0, (), ())}
        if index >= tree.leaves:
            merge = tree.merges[index - tree.leaves]
            for left_choice in plans[merge.left].values():
                for right_choice in plans[merge.right].values():
                    combined = _merge(left_choice, right_choice)
                    if combined.kit_count <= capacity:
                        best[combined.kit_count] = _better(
                            best.get(combined.kit_count), combined)
        if index in qualified:
            own = Choice(1, len(node.members), node.sse, (index,),
                         (tuple(node.members),))
            best[1] = _better(best.get(1), own)
        plans.append(best)
    root_plan = plans[len(tree.nodes) - 1]
    winner = min(root_plan.values(), key=lambda choice: choice.key())
    if winner.kit_count == 0:
        return []
    formation_step = {merge.node: step for step, merge in enumerate(tree.merges, 1)}
    kits = []
    for ordinal, node_index in enumerate(winner.nodes, 1):
        node = tree.nodes[node_index]
        kits.append({
            'node': node_index,
            'members': tuple(node.members),
            'group_pieces': len(node.members),
            'group_sse': node.sse,
            'group_ward_distance': node.height,
            'formation_clusters': tree.leaves - formation_step[node_index],
            'ordinal': ordinal,
        })
    return kits


def selection_sse(kits: list[dict]) -> float:
    return fsum(kit['group_sse'] for kit in kits)
