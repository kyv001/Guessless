# -*- AI Generated -*-
"""guessless 后端的回归测试。

钉住我们讨论后定下来的行为：

- 首击一定安全，首击后可能直接 WON；
- 点到必安格一定翻开（包括非前沿的必安格）；
- 存在必安格时，点非必安格（包括"往里猜"的非前沿格）算输；
- WON / LOST 之后的点击不改变棋盘；
- 合法点击不应抛异常。
"""

import random

import pytest

from guessless import GameState, Minesweeper, Position, Size


def frontier(game: Minesweeper) -> list[Position]:
    """未翻开、且至少与一个已翻开格 8 邻接的格子。"""
    cells: list[Position] = []
    for pos, cell in game.board.items():
        if cell.revealed:
            continue
        if any(game.board[adj].revealed for adj in game.enum_adj(pos)):
            cells.append(pos)
    return cells


def non_frontier(game: Minesweeper) -> list[Position]:
    """未翻开、且不与任何已翻开格相邻的格子。"""
    edge = set(frontier(game))
    return [pos for pos, cell in game.board.items() if not cell.revealed and pos not in edge]


def revealed_count(game: Minesweeper) -> int:
    return sum(cell.revealed for cell in game.board.values())


def reveal_manually(game: Minesweeper, numbers: dict[Position, int]) -> None:
    """构造一个 RUNNING 状态的手工局面（仅用于测试）。"""
    game.state = GameState.RUNNING
    for pos, number in numbers.items():
        cell = game.board[pos]
        cell.revealed = True
        cell.number = number


# --------------------------------------------------------------------------- 首击


def test_initial_state() -> None:
    game = Minesweeper(Size(5, 5), 3)
    assert game.state is GameState.NOTSTARTED
    assert game.t_start is None


@pytest.mark.parametrize(
    ("size", "n_mines"),
    [(Size(5, 5), 2), (Size(9, 9), 1), (Size(9, 9), 10)],
)
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_first_click_is_safe(size: Size, n_mines: int, seed: int) -> None:
    random.seed(seed)
    game = Minesweeper(size, n_mines)
    game.reveal_at(Position(2, 2))
    assert game.board[Position(2, 2)].revealed
    assert game.state in (GameState.RUNNING, GameState.WON)
    assert game.t_start is not None


def test_first_click_can_win_immediately() -> None:
    random.seed(0)
    game = Minesweeper(Size(1, 2), 1)
    game.reveal_at(Position(0, 0))
    assert game.state is GameState.WON


# --------------------------------------------------------------------------- 第二次点击


def test_second_click_on_frontier_reveals() -> None:
    random.seed(0)
    game = Minesweeper(Size(3, 3), 1)
    game.reveal_at(Position(1, 1))
    assert game.board[Position(1, 1)].number == 1
    before = revealed_count(game)
    game.reveal_at(Position(0, 0))
    assert game.board[Position(0, 0)].revealed
    assert revealed_count(game) > before
    assert game.state is not GameState.LOST


def test_second_click_on_frontier_reveals_2x2() -> None:
    random.seed(0)
    game = Minesweeper(Size(2, 2), 1)
    game.reveal_at(Position(0, 0))
    game.reveal_at(Position(1, 0))
    assert game.board[Position(1, 0)].revealed
    assert game.state is not GameState.LOST


@pytest.mark.parametrize("n_mines", [1, 2, 10])
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_frontier_click_never_raises(n_mines: int, seed: int) -> None:
    random.seed(seed)
    game = Minesweeper(Size(9, 9), n_mines)
    game.reveal_at(Position(4, 4))
    cells = frontier(game)
    if not cells:
        pytest.skip("no frontier")
    game.reveal_at(cells[0])
    assert game.state in (GameState.RUNNING, GameState.WON, GameState.LOST)


# --------------------------------------------------------------------------- 非前沿必安格


def test_non_frontier_safe_cell_is_revealed() -> None:
    random.seed(0)
    game = Minesweeper(Size(5, 5), 1)
    game.reveal_at(Position(2, 2))
    assert game.board[Position(2, 2)].number == 1
    target = Position(0, 0)
    assert target in non_frontier(game)
    game.reveal_at(target)
    assert game.state is not GameState.LOST
    assert game.board[target].revealed


def test_winning_non_frontier_move_is_not_overwritten() -> None:
    random.seed(3)
    game = Minesweeper(Size(4, 4), 1)
    game.reveal_at(Position(0, 0))
    assert game.state is GameState.RUNNING
    targets = non_frontier(game)
    assert targets
    game.reveal_at(targets[-1])
    assert game.state is GameState.WON


# --------------------------------------------------------------------------- 规则局面


def make_3x2() -> Minesweeper:
    """所有非前沿格都被两个数字 1 夹住的小局面。"""
    game = Minesweeper(Size(3, 2), 1)
    reveal_manually(game, {Position(0, 0): 1, Position(2, 0): 1})
    return game


def make_3x3_four_mines() -> Minesweeper:
    """3x3、4 雷：既有必安格 b，也有不确定格 d。"""
    game = Minesweeper(Size(3, 3), 4)
    reveal_manually(game, {Position(0, 0): 1, Position(0, 1): 2, Position(1, 2): 2})
    return game


def test_safe_cell_is_revealed_when_other_safe_cells_exist() -> None:
    game = make_3x2()
    game.reveal_at(Position(2, 1))
    assert game.board[Position(2, 1)].revealed
    assert game.state is GameState.RUNNING


def test_guess_when_safe_move_exists_loses() -> None:
    game = make_3x2()
    game.reveal_at(Position(1, 0))
    assert game.state is GameState.LOST


def test_safe_cell_is_revealed_with_mixed_boundary() -> None:
    game = make_3x3_four_mines()
    game.reveal_at(Position(1, 1))
    assert game.board[Position(1, 1)].revealed
    assert game.state is GameState.RUNNING


def test_guess_with_safe_move_loses_on_mixed_boundary() -> None:
    game = make_3x3_four_mines()
    game.reveal_at(Position(2, 1))
    assert game.state is GameState.LOST


def test_guess_into_unrevealed_region_loses_when_safe_move_exists() -> None:
    game = Minesweeper(Size(5, 5), 2)
    reveal_manually(game, {Position(0, 0): 0, Position(1, 1): 1})
    target = Position(4, 4)
    assert target in non_frontier(game)
    game.reveal_at(target)
    assert game.state is GameState.LOST


# --------------------------------------------------------------------------- 终局后的点击


def test_click_after_won_is_ignored() -> None:
    random.seed(0)
    game = Minesweeper(Size(1, 2), 1)
    game.reveal_at(Position(0, 0))
    assert game.state is GameState.WON
    hidden = [pos for pos, cell in game.board.items() if not cell.revealed]
    assert hidden
    before = revealed_count(game)
    game.reveal_at(hidden[0])
    assert game.state is GameState.WON
    assert revealed_count(game) == before


def test_click_after_lost_is_ignored() -> None:
    game = make_3x2()
    game.state = GameState.LOST
    before = revealed_count(game)
    game.reveal_at(Position(2, 1))
    assert game.state is GameState.LOST
    assert revealed_count(game) == before


def test_click_on_revealed_cell_is_ignored() -> None:
    random.seed(0)
    game = Minesweeper(Size(3, 3), 1)
    game.reveal_at(Position(1, 1))
    before_state = game.state
    before = revealed_count(game)
    game.reveal_at(Position(1, 1))
    assert game.state is before_state
    assert revealed_count(game) == before


def test_click_on_flagged_cell_is_ignored() -> None:
    random.seed(0)
    game = Minesweeper(Size(3, 3), 1)
    game.reveal_at(Position(1, 1))
    target = Position(0, 0)
    game.board[target].flagged = True
    before_state = game.state
    before = revealed_count(game)
    game.reveal_at(target)
    assert game.state is before_state
    assert revealed_count(game) == before
    assert not game.board[target].revealed


def test_check_win_does_not_revive_a_lost_game() -> None:
    random.seed(0)
    game = Minesweeper(Size(1, 2), 1)
    game.reveal_at(Position(0, 0))
    assert game.state is GameState.WON
    game.state = GameState.LOST
    game.check_win()
    assert game.state is GameState.LOST
