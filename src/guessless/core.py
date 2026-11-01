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

class Constraint(NamedTuple):
    positions: list[Position]
    sum_mines: int

class Solution(NamedTuple):
    mine_positions: list[Position] # 边界中已确定的雷
    n_extra_mines: int # 非边界位置剩下的雷

class SolveResult:
    def __init__(self, solved_positions: list[Position], unsolved_positions: list[Position], solutions: list[Solution]) -> None:
        self.solved_positions = solved_positions # 已解出的边缘格
        self.unsolved_positions = unsolved_positions # 不确定的内陆格
        self.solutions = solutions

        self.must_be_mine = [pos for pos in solved_positions if     all(pos in solution.mine_positions for solution in self.solutions)]
        self.must_be_safe = [pos for pos in solved_positions if not any(pos in solution.mine_positions for solution in self.solutions)]
        self.cannot_deter = [pos for pos in solved_positions if pos not in self.must_be_safe + self.must_be_mine]

        if not max([solution.n_extra_mines for solution in solutions]): # 所有雷都在边上
            self.must_be_safe.extend(unsolved_positions) # 那么所有非边缘的地方都是安全的

    def sample_safe_at(self, position: Position) -> list[Position]:
        selected_solution = random.choice([solution for solution in self.solutions if position not in solution.mine_positions])
        solved_mines = selected_solution.mine_positions
        unsolved_mines = random.sample([
            pos
            for pos in self.unsolved_positions
            if pos != position
        ], k=selected_solution.n_extra_mines)
        return solved_mines + unsolved_mines

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

    def flag_at(self, position: Position) -> None:
        if self.state != GameState.RUNNING or self.get_at(position).revealed:
            return
        self.get_at(position).flagged ^= True

    def reveal_at(self, position: Position) -> None:
        if self.state == GameState.NOTSTARTED:
            self.state = GameState.RUNNING
            self.t_start = time.time()
            init_mines = random.sample( # 保证第一次必展开
                [pos for pos in self.board if pos not in [position, *self.enum_adj(position)]],
                k=self.n_mines
            )
            self.flood_by(position, init_mines)
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
            if solve_result.cannot_deter: # 边上还有能猜的地方
                if position in solve_result.cannot_deter: # 那么必须猜中
                    self.flood_by(position, solve_result.sample_safe_at(position))
                else: # 如果边缘还有能猜的地方就不准往里猜
                    self.state = GameState.LOST
            else: # 边上都不安全，且没有点到边上
                self.flood_by(position, solve_result.sample_safe_at(position))

    def solve(self) -> SolveResult | None:
        edge_cells: dict[Position, bool | None] = {}
        constraints: list[Constraint] = []
        for y in range(self.size.h):
            for x in range(self.size.w):
                idx = Position(x, y)
                cell = self.get_at(idx)
                if cell.revealed:
                    constraint_sum = cell.number
                    constraint_positions: list[Position] = []
                    for adj_pos in self.enum_adj(idx):
                        if not self.get_at(adj_pos).revealed:
                            edge_cells[adj_pos] = None
                            constraint_positions.append(adj_pos)
                    if constraint_positions:
                        constraints.append(Constraint(positions=constraint_positions, sum_mines=constraint_sum))

        if not edge_cells:
            return None

        edge_positions = [*edge_cells.keys()]
        non_edge_positions = [
            pos for pos in self.board
            if not self.get_at(pos).revealed and
            pos not in edge_positions
        ]

        solutions: list[Solution] = []
        stack: list[tuple[int, bool]] = [(0, True), (0, False)]
        while stack:
            idx, is_mine = stack.pop()
            for pos in edge_positions[idx:]:
                edge_cells[pos] = None # 回退掉之前的尝试
            edge_cells[edge_positions[idx]] = is_mine

            for constraint in constraints:
                actual_sum = 0
                for pos in constraint.positions:
                    if edge_cells[pos] is None:
                        break # 这条限制还有空没填，还不能检查
                    actual_sum += bool(edge_cells[pos])
                else: # 这条限制没有空没填，可以检查
                    if actual_sum != constraint.sum_mines:
                        break # 检查不通过
                # 这一条检查通过，继续下一条
            else: # 每一条检查都通过了，可以继续
                if idx >= len(edge_positions) - 1: # 没得继续了，这就是结果
                    n_solution_mines = sum([bool(v) for k, v in edge_cells.items()])
                    if not (self.n_mines - len(non_edge_positions) <= n_solution_mines <= self.n_mines):
                        continue # 总数过多或者过少
                    solutions.append(Solution([k for k, v in edge_cells.items() if v], self.n_mines - n_solution_mines))
                else: # 还能继续，列举下一个格子
                    stack.extend([(idx + 1, True), (idx + 1, False)])
                    continue

        return SolveResult(solved_positions=edge_positions, unsolved_positions=non_edge_positions, solutions=solutions)

    def check_win(self):
        if self.state != GameState.RUNNING:
            return
        if len([cell for cell in self.board.values() if not cell.revealed]) == self.n_mines:
            self.state = GameState.WON

    def get_at(self, position: Position) -> Cell:
        return self.board[position]

    @staticmethod
    def count_adj(center: Position, targets: list[Position]) -> int:
        return sum(center.is_adj(target) for target in targets)

    def enum_adj(self, center: Position) -> list[Position]:
        adj_positions: list[Position] = []
        for dx, dy in [
            (-1, -1), ( 0, -1), ( 1, -1),
            (-1,  0),           ( 1,  0),
            (-1,  1), ( 0,  1), ( 1,  1)]:
            selected_pos = Position(center.x + dx, center.y + dy)
            if (0 <= selected_pos.x < self.size.w and 0 <= selected_pos.y < self.size.h):
                adj_positions.append(selected_pos)
        return adj_positions

    def flood_by(self, origin: Position, mines: list[Position]):
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
