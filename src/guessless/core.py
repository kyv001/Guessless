from functools import cached_property
from math import comb
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple, Protocol, Self

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

ROOT_STATE = SolveState(-1, ())

class Distribution(dict[int, int]):
    def __add__(self, b):
        result = Distribution()
        for k, v in self.items():
            result[k] += v
        for k, v in b.items():
            result[k] += v
        return result

    def __missing__(self, key: int) -> int:
        return 0

    def __rshift__(self, n: int) -> "Distribution":
        result = Distribution({
            k + n: v
            for k, v in self.items()
        })
        return result

    def __lshift__(self, n: int) -> "Distribution":
        result = Distribution({
            k - n: v
            for k, v in self.items()
        })
        return result

    def __mul__(self, b: Self) -> "Distribution": # pyright: ignore[reportIncompatibleMethodOverride]
        result = Distribution()
        for k1, v1 in self.items():
            for k2, v2 in b.items():
                result[k1 + k2] += v1 * v2
        return result

class SolveNode(NamedTuple):
    #       从此以后的雷数：有此雷数的解数量
    n_mines_dist: Distribution # 用于指导“按特定雷数采样”
    #     从此以后的雷数：{位置：此处有雷的解数量}
    pos_mines_dist: dict[int, Distribution] # 用于统计 must_be_*

    def next_node(self, idx: int, is_mine: bool, n_cells: int):
        n_mines_dist = self.n_mines_dist >> int(is_mine)
        pos_mines_dist = {
            k + is_mine: Distribution({
                p: v if p == idx else self.pos_mines_dist[k][p]
                for p in range(idx, n_cells)
                if p in self.pos_mines_dist.get(k, {}) or (is_mine and p == idx)
            })
            for k, v in self.n_mines_dist.items()
        }
        return SolveNode(n_mines_dist, pos_mines_dist)

    def __add__(self, b: Self) -> "SolveNode": # pyright: ignore[reportIncompatibleMethodOverride]
        node = SolveNode(
            self.n_mines_dist + b.n_mines_dist,
            defaultdict(lambda: Distribution())
        )
        for k, d in self.pos_mines_dist.items():
            node.pos_mines_dist[k] += d
        for k, d in b.pos_mines_dist.items():
            node.pos_mines_dist[k] += d
        return node

type Table = dict[SolveState, SolveNode]

class Component(Protocol):
    positions: list[Position]
    def solve(self) -> "ComponentResult": ...

class ComponentResult(Protocol):
    @property
    def component(self) -> Component: ...
    @property
    def n_mines_dist(self) -> Distribution: ...
    def position_mine_count(self, n_mines: int, idx: int) -> int: ...
    def sample(self, n_mines: int, safe_positions: set[Position] | None = None, mine_positions: set[Position] | None = None) -> set[Position]: ...

@dataclass # 不是NamedTuple因为不修改地创建很麻烦
class SolvedComponent:
    positions: list[Position] # 保持顺序以利用扫雷空间局部性
    constraints: list[Constraint]

    def solve(self) -> "SolvedComponentResult":
        stack: list[SolveAttempt] = [SolveAttempt(0, True, False), SolveAttempt(0, False, False)]
        node_stack: list[SolveNode] = [SolveNode(Distribution(), {})]
        component_cells: dict[Position, bool | None] = {position: None for position in self.positions}
        active_constraints: list[list[Constraint]] = [[] for _ in self.positions]
        for constraint in self.constraints:
            indexes = [self.positions.index(position) for position in constraint.positions]
            for idx in range(min(indexes), max(indexes) + 1):
                active_constraints[idx].append(constraint)
        table: Table = defaultdict(lambda: SolveNode(Distribution(), defaultdict(lambda: Distribution())))
        while stack:
            idx, is_mine, finished = stack.pop()
            component_cells[self.positions[idx]] = is_mine

            constraints_state = tuple(
                sum(1 for pos in constraint.positions if component_cells[pos])
                for constraint in active_constraints[idx]
            )
            state = SolveState(idx, constraints_state)

            if finished:
                current = node_stack.pop()
                table[state] = current
                father = node_stack.pop()
                node_stack.append(father + current.next_node(idx, is_mine, len(component_cells)))
                component_cells[self.positions[idx]] = None # 回溯前清理
                continue

            if state in table:
                current = table[state]
                father = node_stack.pop()
                node_stack.append(father + current.next_node(idx, is_mine, len(component_cells)))
                component_cells[self.positions[idx]] = None # 回溯前清理
                continue

            for constraint in active_constraints[idx]:
                unfinished_sum = 0
                actual_sum = 0
                constraint_size = len(constraint.positions)
                for i, pos in enumerate(constraint.positions):
                    if component_cells[pos] is None:
                        unfinished_sum += 1 # 这条限制还有空没填
                    actual_sum += bool(component_cells[pos])
                    if actual_sum > constraint.sum_mines:
                        break # 无论是否有没填完都已经超过了
                    if i + 1 - actual_sum - unfinished_sum > constraint_size - constraint.sum_mines:
                        break # 即使剩下的全部填满都不可能够
                else:
                    continue # 这一条检查通过，继续下一条
                component_cells[self.positions[idx]] = None
                break # 检查不通过，回到上一分支
            else: # 每一条检查都通过了，可以继续
                if idx >= len(self.positions) - 1: # 没得继续了
                    current = SolveNode(Distribution({0: 1}), {})
                    table[state] = current
                    father = node_stack.pop()
                    node_stack.append(father + current.next_node(idx, is_mine, len(component_cells)))
                    component_cells[self.positions[idx]] = None # 回溯前清理
                else: # 还能继续，列举下一个格子
                    node_stack.append(SolveNode(Distribution(), {}))
                    stack.extend([SolveAttempt(idx, is_mine, True), SolveAttempt(idx + 1, True, False), SolveAttempt(idx + 1, False, False)])
        table[ROOT_STATE] = node_stack[0]
        return SolvedComponentResult(self, table)

class SolvedComponentResult(NamedTuple):
    component: SolvedComponent
    table: Table

    def position_mine_count(self, n_mines: int, idx: int) -> int:
        return self.table[ROOT_STATE].pos_mines_dist[n_mines][idx]

    @property
    def n_mines_dist(self) -> Distribution:
        return self.table[ROOT_STATE].n_mines_dist

    def sample(self, n_mines: int, safe_positions: set[Position] | None = None, mine_positions: set[Position] | None = None) -> set[Position]:
        raise NotImplementedError

@dataclass
class UnsolvedComponent:
    positions: list[Position]

    def solve(self) -> "UnsolvedComponentResult":
        return UnsolvedComponentResult(component=self)

class UnsolvedComponentResult(NamedTuple):
    component: UnsolvedComponent

    def position_mine_count(self, n_mines: int, idx: int) -> int:
        return comb(len(self.component.positions) - 1, n_mines - 1)

    @property
    def n_mines_dist(self) -> Distribution:
        return Distribution({
            k: comb(len(self.component.positions), k)
            for k in range(len(self.component.positions) + 1)
        })

    def sample(self, n_mines: int, safe_positions: set[Position] | None = None, mine_positions: set[Position] | None = None) -> set[Position]:
        safe_positions = safe_positions or set()
        mine_positions = mine_positions or set()
        return {*random.sample(
            list(set(self.component.positions) - safe_positions - mine_positions),
            k=len(self.component.positions) - len(mine_positions)
        )} | mine_positions

class SolveResult:
    def __init__(self, total_mines: int, component_results: list[ComponentResult] | None,
                must_be_safe: set[Position] | None = None, must_be_mine: set[Position] | None = None) -> None:
        self.must_be_mine = must_be_mine or set()
        self.must_be_safe = must_be_safe or set()
        self.compoent_results = component_results
        self.total_mines = total_mines

        self.mines_distributions = [Distribution({0: 1})]
        if component_results:
            for cr in component_results[::-1]:
                self.mines_distributions.insert(0, cr.n_mines_dist * self.mines_distributions[0])

    def sample(self, safe_positions: set[Position] | None = None, mine_positions: set[Position] | None = None) -> set[Position]:
        raise NotImplementedError

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
                self.flood_by(position, solve_result.sample(safe_positions={position}))
            else: # 那就不准猜
                self.state = GameState.LOST
        else: # 没有必定安全的地方
            if solve_result.cannot_deter and position in solve_result.cannot_deter: # 还有能猜的地方
                self.flood_by(position, solve_result.sample(safe_positions={position})) # 那么必须猜中
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

        unsolved_component = UnsolvedComponent([*non_edge_positions])

        if not solve_positions: # 不需要回溯了
            result = SolveResult(
                total_mines=self.n_mines,
                component_results=[unsolved_component.solve()],
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

        position_components: dict[Position, SolvedComponent] = {}
        for position in solve_positions:
            root = find(position)
            if root not in position_components:
                position_components[root] = SolvedComponent([position], [])
            else:
                position_components[root].positions.append(position)
        for constraint in constraints:
            root = find(next(iter(constraint.positions)))
            position_components[root].constraints.append(constraint)
        components = [*position_components.values()]

        # --- 第三步：回溯 ---
        component_results = [component.solve() for component in components]

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
