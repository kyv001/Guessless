from collections import defaultdict
from itertools import product
from math import comb
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

type Board = dict[Position, Cell]

class Size(NamedTuple):
    w: int
    h: int

class Position(NamedTuple):
    x: int
    y: int

    def is_adj(self, position: "Position") -> bool:
        return max((abs(position.x - self.x), abs(position.y - self.y))) == 1

@dataclass
class Constraint:
    positions: set[Position]
    sum_mines: int

class Solution(NamedTuple):
    mine_positions: set[Position] # 已确定的雷
    n_extra_mines: int # 剩下不确定的雷

class SolveAttempt(NamedTuple):
    idx: int
    value: bool
    finished: bool

class SolveState(NamedTuple):
    idx: int # 当前填的格子
    constraints_state: tuple[int, ...]

ROOT_STATE = SolveState(-1, ()) # 虚拟根：F(-1) = 整个组件的分布

class SolveNode(NamedTuple):
    #       从此以后的雷数：有此雷数的解数量
    n_mines_dist: dict[int, int] # 用于指导“按特定雷数采样”
    #     从此以后的雷数：{位置：此处有雷的解数量}
    pos_mines_dist: dict[int, dict[int, int]] # 用于统计 must_be_*

    def next_node(self, idx: int, is_mine: bool, n_cells: int):
        n_mines_dist = {k + is_mine: v for k, v in self.n_mines_dist.items()}
        pos_mines_dist = {
            k + is_mine: {
                p: v if p == idx else self.pos_mines_dist[k][p]
                for p in range(idx, n_cells)
                if p in self.pos_mines_dist.get(k, {}) or (is_mine and p == idx)
            }
            for k, v in self.n_mines_dist.items()
        }
        return SolveNode(n_mines_dist, pos_mines_dist)

    def __add__(self, b):
        node = SolveNode(defaultdict(int), defaultdict(lambda: defaultdict(int)))
        for k, v in self.n_mines_dist.items():
            node.n_mines_dist[k] += v
        for k, v in b.n_mines_dist.items():
            node.n_mines_dist[k] += v
        for k, d in self.pos_mines_dist.items():
            for p, v in d.items():
                node.pos_mines_dist[k][p] += v
        for k, d in b.pos_mines_dist.items():
            for p, v in d.items():
                node.pos_mines_dist[k][p] += v
        return node

type Table = dict[SolveState, SolveNode]

@dataclass # 不是NamedTuple因为不修改地创建很麻烦
class Component:
    positions: list[Position] # 保持顺序以利用扫雷空间局部性
    constraints: list[Constraint]

class ComponentResult(NamedTuple):
    component: Component
    table: Table

def comb0(n: int, k: int) -> int:
    if n < 0 or k < 0 or k > n:
        return 0
    return comb(n, k)

def convolve(a: dict[int, int], b: dict[int, int]) -> dict[int, int]:
    result: dict[int, int] = {}
    for ka, va in a.items():
        for kb, vb in b.items():
            result[ka + kb] = result.get(ka + kb, 0) + va * vb
    return result

def convolve_all(distributions: list[dict[int, int]]) -> dict[int, int]:
    result: dict[int, int] = {0: 1}
    for distribution in distributions:
        result = convolve(result, distribution)
    return result

class ComponentLayout(NamedTuple):
    positions: list[Position]
    constraints: list[Constraint]
    active_at: list[list[int]]

def component_layout(component: Component) -> ComponentLayout:
    positions = component.positions
    constraints = component.constraints
    index_of = {position: index for index, position in enumerate(positions)}
    active_at: list[list[int]] = [[] for _ in positions]
    for ci, constraint in enumerate(constraints):
        indexes = [index_of[position] for position in constraint.positions]
        if not indexes:
            continue
        for idx in range(min(indexes), max(indexes) + 1):
            active_at[idx].append(ci)
    return ComponentLayout(positions, constraints, active_at)

def component_transition(layout: ComponentLayout, idx: int, sums: tuple[int, ...], value: bool) -> tuple[int, ...]:
    previous = layout.active_at[idx]
    following = layout.active_at[idx + 1]
    positions = layout.positions
    constraints = layout.constraints
    result: list[int] = []
    for ci in following:
        if ci in previous:
            slot = previous.index(ci)
            constraint = constraints[ci]
            delta = 1 if value and positions[idx + 1] in constraint.positions else 0
            result.append(sums[slot] + delta)
        else:
            # 新活跃约束的 min 就是 idx + 1，所以第 idx+1 格一定在约束里
            result.append(1 if value else 0)
    return tuple(result)

def solve_component(component: Component) -> Table:
    """对单个连通分量做 DP，返回 state -> F(state) 的表，并在 ROOT_STATE 放组件汇总。"""
    layout = component_layout(component)
    positions = layout.positions
    constraints = layout.constraints
    active_at = layout.active_at
    n_cells = len(positions)
    table: Table = defaultdict(lambda: SolveNode(defaultdict(int), defaultdict(lambda: defaultdict(int))))
    if n_cells == 0:
        table[ROOT_STATE] = SolveNode({0: 1}, {})
        return table

    stack: list[SolveAttempt] = [SolveAttempt(0, True, False), SolveAttempt(0, False, False)]
    node_stack: list[SolveNode] = [SolveNode({}, {})]
    cells: dict[Position, bool | None] = {position: None for position in positions}
    while stack:
        idx, is_mine, finished = stack.pop()
        cells[positions[idx]] = is_mine
        state = SolveState(idx, tuple(
            sum(1 for pos in constraints[ci].positions if cells[pos])
            for ci in active_at[idx]
        ))

        if finished:
            current = node_stack.pop()
            table[state] = current
            father = node_stack.pop()
            node_stack.append(father + current.next_node(idx, is_mine, n_cells))
            cells[positions[idx]] = None
            continue

        if state in table:
            current = table[state]
            father = node_stack.pop()
            node_stack.append(father + current.next_node(idx, is_mine, n_cells))
            cells[positions[idx]] = None
            continue

        valid = True
        for ci in active_at[idx]:
            constraint = constraints[ci]
            unfinished = 0
            actual = 0
            size = len(constraint.positions)
            for i, pos in enumerate(constraint.positions):
                if cells[pos] is None:
                    unfinished += 1
                actual += bool(cells[pos])
                if actual > constraint.sum_mines:
                    valid = False
                    break
                if i + 1 - actual - unfinished > size - constraint.sum_mines:
                    valid = False
                    break
            if not valid:
                break

        if not valid:
            cells[positions[idx]] = None
            continue

        if idx >= n_cells - 1:
            current = SolveNode({0: 1}, {})
            table[state] = current
            father = node_stack.pop()
            node_stack.append(father + current.next_node(idx, is_mine, n_cells))
            cells[positions[idx]] = None
        else:
            node_stack.append(SolveNode({}, {}))
            stack.extend([
                SolveAttempt(idx, is_mine, True),
                SolveAttempt(idx + 1, True, False),
                SolveAttempt(idx + 1, False, False),
            ])

    table[ROOT_STATE] = node_stack[0]
    return table

def weighted_choice(options: list, weights: list[int]):
    total = sum(weights)
    if total <= 0:
        raise ValueError("no valid choice")
    pick = random.randrange(total)
    for option, weight in zip(options, weights):
        pick -= weight
        if pick < 0:
            return option
    return options[-1]

def sample_component(component: Component, table: Table, n_mines: int) -> set[Position]:
    layout = component_layout(component)
    positions = layout.positions
    active_at = layout.active_at
    n_cells = len(positions)
    if n_cells == 0:
        if n_mines != 0:
            raise ValueError("empty component cannot contain mines")
        return set()

    mines: set[Position] = set()
    root_options: list[bool] = []
    root_weights: list[int] = []
    root_sums: list[tuple[int, ...]] = []
    for value in (False, True):
        sums = tuple(1 if value else 0 for _ in active_at[0])
        node = table.get(SolveState(0, sums))
        weight = node.n_mines_dist.get(n_mines - value, 0) if node is not None else 0
        root_options.append(value)
        root_weights.append(weight)
        root_sums.append(sums)
    choice = weighted_choice(root_options, root_weights)
    if choice:
        mines.add(positions[0])
    sums = root_sums[0] if choice is False else root_sums[1]
    remaining = n_mines - choice

    for idx in range(n_cells - 1):
        options: list[bool] = []
        weights: list[int] = []
        next_sums_list: list[tuple[int, ...]] = []
        for value in (False, True):
            next_sums = component_transition(layout, idx, sums, value)
            node = table.get(SolveState(idx + 1, next_sums))
            weight = node.n_mines_dist.get(remaining - value, 0) if node is not None else 0
            options.append(value)
            weights.append(weight)
            next_sums_list.append(next_sums)
        choice = weighted_choice(options, weights)
        if choice:
            mines.add(positions[idx + 1])
        sums = next_sums_list[0] if choice is False else next_sums_list[1]
        remaining -= choice

    if remaining != 0:
        raise RuntimeError("sampled component has inconsistent mine count")
    return mines

def enumerate_component_solutions(component: Component, table: Table) -> list[set[Position]]:
    layout = component_layout(component)
    positions = layout.positions
    active_at = layout.active_at
    n_cells = len(positions)
    if n_cells == 0:
        return [set()]
    solutions: list[set[Position]] = []

    def dfs(idx: int, sums: tuple[int, ...], mines: set[Position]) -> None:
        if idx == n_cells - 1:
            solutions.append(set(mines))
            return
        for value in (False, True):
            next_sums = component_transition(layout, idx, sums, value)
            child = table.get(SolveState(idx + 1, next_sums))
            if child is None or not child.n_mines_dist:
                continue
            if value:
                mines.add(positions[idx + 1])
            dfs(idx + 1, next_sums, mines)
            if value:
                mines.discard(positions[idx + 1])

    for value in (False, True):
        sums = tuple(1 if value else 0 for _ in active_at[0])
        root = table.get(SolveState(0, sums))
        if root is None or not root.n_mines_dist:
            continue
        mines: set[Position] = set()
        if value:
            mines.add(positions[0])
        dfs(0, sums, mines)
    return solutions

class SolveResult:
    def __init__(self, total_mines: int, solved_positions: set[Position], unsolved_positions: set[Position],
                component_results: list[ComponentResult] | None, must_be_safe: set[Position] | None = None,
                must_be_mine: set[Position] | None = None) -> None:
        self.total_mines = total_mines
        self.solved_positions = set(solved_positions)
        self.unsolved_positions = set(unsolved_positions)
        self.component_results = component_results or []
        self.all_positions = self.solved_positions | self.unsolved_positions

        fixed_mine = set(must_be_mine or ()) & self.all_positions
        fixed_safe = (set(must_be_safe or ()) & self.all_positions) - fixed_mine
        self.component_positions: set[Position] = set()
        self.summaries: list[SolveNode] = []
        for component_result in self.component_results:
            self.component_positions.update(component_result.component.positions)
            self.summaries.append(component_result.table[ROOT_STATE])
        # 组件内部的格子由 DP 负责；fixed_* 只保留组件外的固定格。
        self.fixed_mine = fixed_mine - self.component_positions
        self.fixed_safe = fixed_safe - self.component_positions
        self.free_non_edge = self.unsolved_positions - self.fixed_mine - self.fixed_safe

        self.must_be_mine: set[Position] = set(self.fixed_mine)
        self.must_be_safe: set[Position] = set(self.fixed_safe)
        self.cannot_deter: set[Position] = set()
        self.total_valid: int = 0
        self._solutions_cache: list[Solution] | None = None
        self._compute()
        self.must_be_safe -= self.must_be_mine
        self.cannot_deter = self.all_positions - self.must_be_mine - self.must_be_safe

    def _compute(self) -> None:
        fixed_count = len(self.fixed_mine)
        free_count = len(self.free_non_edge)
        distributions = [summary.n_mines_dist for summary in self.summaries]
        all_dist = convolve_all(distributions)

        total_valid = 0
        for component_mines, count in all_dist.items():
            extra = self.total_mines - fixed_count - component_mines
            if 0 <= extra <= free_count:
                total_valid += count * comb0(free_count, extra)
        self.total_valid = total_valid

        if total_valid == 0:
            # 局面数据不一致：沿用旧行为，让任何点击都判负，而不是静默产生非法采样。
            self.must_be_mine = set(self.all_positions)
            self.must_be_safe = set(self.all_positions)
            return

        component_count = len(self.summaries)
        prefix: list[dict[int, int]] = [{0: 1}]
        for distribution in distributions:
            prefix.append(convolve(prefix[-1], distribution))
        suffix: list[dict[int, int]] = [{} for _ in range(component_count + 1)]
        suffix[component_count] = {0: 1}
        for i in range(component_count - 1, -1, -1):
            suffix[i] = convolve(distributions[i], suffix[i + 1])

        for i, component_result in enumerate(self.component_results):
            summary = self.summaries[i]
            others = convolve(prefix[i], suffix[i + 1])
            weights: dict[int, int] = {}
            for component_mines in summary.n_mines_dist:
                weight = 0
                for other_mines, count in others.items():
                    extra = self.total_mines - fixed_count - component_mines - other_mines
                    if 0 <= extra <= free_count:
                        weight += count * comb0(free_count, extra)
                weights[component_mines] = weight

            for local_idx, position in enumerate(component_result.component.positions):
                mine_count = 0
                for component_mines, weight in weights.items():
                    mine_count += summary.pos_mines_dist.get(component_mines, {}).get(local_idx, 0) * weight
                if mine_count == 0:
                    self.must_be_safe.add(position)
                elif mine_count == total_valid:
                    self.must_be_mine.add(position)

        if free_count:
            free_mine_count = 0
            free_safe_count = 0
            for component_mines, count in all_dist.items():
                extra = self.total_mines - fixed_count - component_mines
                if 0 <= extra <= free_count:
                    free_mine_count += count * comb0(free_count - 1, extra - 1)
                    free_safe_count += count * comb0(free_count - 1, extra)
            if free_mine_count == 0:
                self.must_be_safe.update(self.free_non_edge)
            elif free_safe_count == 0:
                self.must_be_mine.update(self.free_non_edge)

    @property
    def solutions(self) -> list[Solution]:
        """旧接口兼容：只在被访问时枚举（测试/复盘用），正常求解不走这里。"""
        if self._solutions_cache is None:
            component_solutions = [
                enumerate_component_solutions(component_result.component, component_result.table)
                for component_result in self.component_results
            ]
            combinations = [()] if not component_solutions else product(*component_solutions)
            result: list[Solution] = []
            for combination in combinations:
                mines = set(self.fixed_mine)
                for local_mines in combination:
                    mines |= local_mines
                extra = self.total_mines - len(mines)
                if 0 <= extra <= len(self.free_non_edge):
                    result.append(Solution(set(mines), extra))
            self._solutions_cache = result
        return self._solutions_cache

    def sample_safe_at(self, position: Position) -> set[Position]:
        if self.total_valid == 0:
            raise ValueError("no valid configuration")
        if position in self.must_be_mine:
            raise ValueError("cannot sample a safe configuration for a must-be-mine position")

        component_index: int | None = None
        for i, component_result in enumerate(self.component_results):
            if position in component_result.component.positions:
                component_index = i
                break

        components: list[Component] = []
        tables: list[Table] = []
        summaries: list[SolveNode] = []
        for i, component_result in enumerate(self.component_results):
            if i == component_index:
                component = Component(
                    component_result.component.positions,
                    [*component_result.component.constraints, Constraint({position}, 0)],
                )
                table = solve_component(component)
            else:
                component = component_result.component
                table = component_result.table
            components.append(component)
            tables.append(table)
            summaries.append(table[ROOT_STATE])

        free_pool = set(self.free_non_edge)
        free_pool.discard(position)
        fixed_count = len(self.fixed_mine)
        free_count = len(free_pool)

        all_dist = convolve_all([summary.n_mines_dist for summary in summaries])
        total_options: list[int] = []
        total_weights: list[int] = []
        for component_mines, count in all_dist.items():
            extra = self.total_mines - fixed_count - component_mines
            if 0 <= extra <= free_count:
                total_options.append(component_mines)
                total_weights.append(count * comb0(free_count, extra))
        sampled_total = weighted_choice(total_options, total_weights)
        extra = self.total_mines - fixed_count - sampled_total

        suffix: list[dict[int, int]] = [{} for _ in range(len(summaries) + 1)]
        suffix[len(summaries)] = {0: 1}
        for i in range(len(summaries) - 1, -1, -1):
            suffix[i] = convolve(summaries[i].n_mines_dist, suffix[i + 1])

        mines: set[Position] = set(self.fixed_mine)
        remaining = sampled_total
        for i, (component, table, summary) in enumerate(zip(components, tables, summaries)):
            options: list[int] = []
            weights: list[int] = []
            for component_mines, count in summary.n_mines_dist.items():
                rest = remaining - component_mines
                if rest < 0:
                    continue
                weight = count * suffix[i + 1].get(rest, 0)
                if weight:
                    options.append(component_mines)
                    weights.append(weight)
            chosen = weighted_choice(options, weights)
            remaining -= chosen
            mines |= sample_component(component, table, chosen)
        if remaining != 0:
            raise RuntimeError("sampled mine counts do not add up")

        mines |= set(random.sample(sorted(free_pool), extra))
        if position in mines or len(mines) != self.total_mines:
            raise RuntimeError("sampled configuration is inconsistent")
        return mines

@dataclass
class Cell:
    revealed: bool
    number: int
    flagged: bool

class GameState(Enum):
    NOTSTARTED = 0
    RUNNING = 1
    WON = 2
    LOST = 3

class Minesweeper:
    def __init__(self, size: Size, n_mines: int) -> None:
        self.size = size
        self.n_mines = n_mines
        self.board: Board = {
            (Position(x, y)): Cell(revealed=False, number=0, flagged=False)
            for x in range(size.w)
            for y in range(size.h)
        }
        self.state = GameState.NOTSTARTED
        self.t_start: float | None = None
        self.last_solve_result: SolveResult | None = None
        self.must_be_mine: set[Position] = set()
        self.must_be_safe: set[Position] = set()

    def flag_at(self, position: Position) -> None:
        if self.state != GameState.RUNNING or self.get_at(position).revealed:
            return
        self.get_at(position).flagged ^= True

    def reveal_at(self, position: Position, skip_solve: bool = False) -> None:
        if self.state == GameState.NOTSTARTED:
            self.state = GameState.RUNNING
            self.t_start = time.time()
            init_mines = random.sample( # 保证第一次必展开
                [pos for pos in self.board if pos not in [position, *self.enum_adj(position)]],
                k=self.n_mines
            )
            self.flood_by(position, set(init_mines))
            return
        if self.state != GameState.RUNNING or self.get_at(position).flagged:
            return

        # Chord
        if (cell := self.get_at(position)).revealed:
            neighbours = self.enum_adj(position)
            if ((number := cell.number) and
                sum([self.get_at(pos).flagged for pos in neighbours]) == number):
                for pos in neighbours:
                    if not (cell := self.get_at(pos)).flagged and not cell.revealed:
                        self.reveal_at(pos)
            return

        if skip_solve and self.last_solve_result is not None:
            solve_result = self.last_solve_result
        else:
            solve_result = self.solve()
        if solve_result is None:
            self.check_win()
            return

        if position in solve_result.must_be_mine:
            self.state = GameState.LOST
            return
        if solve_result.must_be_safe: # 有必定安全的地方
            if position in solve_result.must_be_safe:
                self.flood_by(position, solve_result.sample_safe_at(position))
            else: # 那就不准猜
                self.state = GameState.LOST
        else: # 没有必定安全的地方
            if solve_result.cannot_deter and position in solve_result.cannot_deter: # 还有能猜的地方
                self.flood_by(position, solve_result.sample_safe_at(position)) # 那么必须猜中
        self.last_solve_result = None # 发生了状态改变，使缓存过期

    def solve(self) -> SolveResult | None:
        edge_positions: list[Position] = [] # 保持顺序以利用扫雷空间局部性
        constraints: list[Constraint] = []
        for y in range(self.size.h):
            for x in range(self.size.w):
                idx = Position(x, y)
                cell = self.get_at(idx)
                if cell.revealed:
                    constraint_sum = cell.number
                    constraint_positions: set[Position] = set()
                    for adj_pos in self.enum_adj(idx):
                        if not self.get_at(adj_pos).revealed:
                            if adj_pos not in edge_positions:
                                edge_positions.append(adj_pos)
                            constraint_positions.add(adj_pos)
                    if constraint_positions:
                        constraints.append(Constraint(positions=constraint_positions, sum_mines=constraint_sum))

        if not edge_positions:
            return None

        non_edge_positions = {
            pos for pos in self.board
            if not self.get_at(pos).revealed and
            pos not in edge_positions
        }

        # --- 第一步：传播 ---
        while True:
            changed: bool = False
            # 1. 消元
            for i, constraint_a in enumerate(constraints):
                for constraint_b in constraints[i:]:
                    if constraint_b.positions > constraint_a.positions:
                        constraint_b.positions -= constraint_a.positions
                        constraint_b.sum_mines -= constraint_a.sum_mines
                        changed = True
                    elif constraint_a.positions > constraint_b.positions:
                        constraint_a.positions -= constraint_b.positions
                        constraint_a.sum_mines -= constraint_b.sum_mines
                        changed = True

            # 2. 可直接得出结论的约束
            for constraint in [*constraints]:
                if constraint.sum_mines == 0:
                    self.must_be_safe.update(constraint.positions)
                    constraints.remove(constraint)
                    changed = True
                elif constraint.sum_mines == len(constraint.positions):
                    self.must_be_mine.update(constraint.positions)
                    constraints.remove(constraint)
                    changed = True

            # 3. 结论传播给其他约束
            for constraint in constraints:
                for pos in [*constraint.positions]:
                    if pos in self.must_be_mine:
                        constraint.positions.remove(pos)
                        constraint.sum_mines -= 1
                        changed = True
                    elif pos in self.must_be_safe:
                        constraint.positions.remove(pos)
                        changed = True

            if not changed: break

        solve_positions: list[Position] = [
            position for position in edge_positions
            if position not in (self.must_be_mine | self.must_be_safe)
        ] # 此处必须保留原始扫描顺序：扫描顺序把同一个约束的格子放在一起，让剪枝可行

        if not solve_positions: # 不需要回溯了
            result = SolveResult(
                total_mines=self.n_mines,
                solved_positions={*edge_positions},
                unsolved_positions=non_edge_positions,
                component_results=None,
                must_be_mine=self.must_be_mine,
                must_be_safe=self.must_be_safe
            )
            self.last_solve_result = result
            return result

        # --- 第二步：分量分解 ---
        parents: dict[Position, Position] = {position: position for position in solve_positions}
        sizes: dict[Position, int] = {position: 1 for position in solve_positions}

        def find(position: Position) -> Position:
            root = position
            while parents[root] != root:
                root = parents[root]
            while parents[position] != position:
                parents[position], position = root, parents[position]
            return root

        for constraint in constraints:
            positions_iter = iter(constraint.positions)
            a = next(positions_iter)
            ra = find(a)
            for b in positions_iter:
                rb = find(b)
                if sizes[ra] < sizes[rb]:
                    ra, rb = rb, ra
                parents[rb] = ra
                sizes[ra] += sizes[rb]

        position_components: dict[Position, Component] = {}
        for position in solve_positions:
            root = find(position)
            if root not in position_components:
                position_components[root] = Component([position], [])
            else:
                position_components[root].positions.append(position)
        for constraint in constraints:
            root = find(next(iter(constraint.positions)))
            position_components[root].constraints.append(constraint)
        components = [*position_components.values()]

        # --- 第三步：回溯 ---
        component_results: list[ComponentResult] = [
            ComponentResult(component, solve_component(component))
            for component in components
        ]

        result = SolveResult(
            total_mines=self.n_mines,
            solved_positions={*edge_positions},
            unsolved_positions=non_edge_positions,
            component_results=component_results,
            must_be_mine=self.must_be_mine,
            must_be_safe=self.must_be_safe
        )
        self.last_solve_result = result
        self.must_be_mine.update(result.must_be_mine)
        self.must_be_safe.update(result.must_be_safe)
        return result

    def check_win(self):
        if self.state != GameState.RUNNING:
            return
        if len([cell for cell in self.board.values() if not cell.revealed]) == self.n_mines:
            self.state = GameState.WON

    def get_at(self, position: Position) -> Cell:
        return self.board[position]

    @staticmethod
    def count_adj(center: Position, targets: set[Position]) -> int:
        return sum(center.is_adj(target) for target in targets)

    def enum_adj(self, center: Position) -> set[Position]:
        adj_positions: set[Position] = set()
        for dx, dy in [
            (-1, -1), ( 0, -1), ( 1, -1),
            (-1,  0),           ( 1,  0),
            (-1,  1), ( 0,  1), ( 1,  1)]:
            selected_pos = Position(center.x + dx, center.y + dy)
            if (0 <= selected_pos.x < self.size.w and 0 <= selected_pos.y < self.size.h):
                adj_positions.add(selected_pos)
        return adj_positions

    def flood_by(self, origin: Position, mines: set[Position]):
        stack: list[Position] = [origin]
        while stack:
            pos = stack.pop()
            cell = self.get_at(pos)
            if cell.revealed or cell.flagged: continue

            cell.revealed = True
            cell.flagged = False
            cell.number = self.count_adj(pos, mines)
            if cell.number > 0: continue

            for next_pos in self.enum_adj(pos):
                if not self.get_at(next_pos).revealed:
                    stack.append(next_pos)
        self.check_win()

__all__ = [
    "Board",
    "Cell",
    "Constraint",
    "GameState",
    "Minesweeper",
    "Position",
    "Size",
    "Solution",
    "SolveResult"
]
