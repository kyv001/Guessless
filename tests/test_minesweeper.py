# -*- AI Generated -*-
"""guessless 后端的回归测试。

钉住我们讨论后定下来的行为：

- 首击一定安全；空间足够时保证开出 0 区域（不测试“排除邻域都不够”的极端局）；
- 点到必安格一定翻开（包括非前沿的必安格）；
- 存在必安格时，点非必安格（包括"往里猜"的非前沿格）算输；
- WON / LOST 之后的点击不改变棋盘；
- 合法点击不应抛异常。

失败时会附带棋盘快照（状态、雷数、已翻开格、隐藏/插旗格、n_extra 取值等），
避免只看到一句 assert 却不知道当时局面长什么样。
"""

import random

import pytest

from guessless.core import GameState, Minesweeper, Position, Size


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


# --------------------------------------------------------------- 失败时的诊断信息


def _fmt_positions(positions: list[Position]) -> str:
    return "[" + ", ".join(f"({pos.x},{pos.y})" for pos in positions) + "]"


def _cell_symbol(game: Minesweeper, pos: Position) -> str:
    cell = game.board[pos]
    if cell.revealed:
        return "." if cell.number == 0 else str(cell.number)
    return "F" if cell.flagged else "#"


def _solve_summary(game: Minesweeper) -> str:
    """在不至于枚举爆炸的前提下，把 solver 的 deductions 拼成一行。"""
    try:
        cells = frontier(game)
        if not cells:
            return "solve: (no frontier)"
        if len(cells) > 18:
            return f"solve: (frontier={len(cells)} too large to dump)"
        result = game.solve()
    except Exception as exc:  # noqa: BLE001 - 诊断信息需要报告任何异常
        return f"solve: raised {type(exc).__name__}: {exc}"

    if result is None:
        return "solve: (no frontier)"
    extra = sorted({solution.n_extra_mines for solution in result.solutions})
    return (
        f"solve: solutions={len(result.solutions)} n_extra={extra} "
        f"must_mine={_fmt_positions(result.must_be_mine)} "
        f"must_safe={_fmt_positions(result.must_be_safe)} "
        f"cannot_deter={_fmt_positions(result.cannot_deter)} "
        f"unsolved={_fmt_positions(result.unsolved_positions)}"
    )


def puzzle_snapshot(game: Minesweeper, expectation: str = "") -> str:
    """失败断言用的可读棋盘快照。"""
    lines = [
        f"state={game.state.name} n_mines={game.n_mines} "
        f"revealed={revealed_count(game)}/{len(game.board)} t_start={game.t_start!r}",
    ]
    if expectation:
        lines.append(f"expected: {expectation}")

    header = "      " + " ".join(f"{x:>2}" for x in range(game.size.w))
    lines.append(header)
    for y in range(game.size.h):
        row = " ".join(f"{_cell_symbol(game, Position(x, y)):>2}" for x in range(game.size.w))
        lines.append(f"y={y:>2} |" + row)

    lines.append("legend: #=hidden F=flagged .=revealed-0 1-8=number")
    lines.append(_solve_summary(game))
    return "\n".join(lines)


def reveal(game: Minesweeper, position: Position) -> None:
    """调用 reveal_at，并把异常转换成带棋盘快照的 AssertionError。"""
    try:
        game.reveal_at(position)
    except Exception as exc:  # noqa: BLE001 - 测试需要把任何异常变成可读信息
        raise AssertionError(
            f"reveal_at({position.x},{position.y}) raised {type(exc).__name__}: {exc}\n"
            + puzzle_snapshot(game, f"reveal_at({position.x},{position.y})")
        ) from exc


# --------------------------------------------------------------------------- 首击


def test_initial_state() -> None:
    game = Minesweeper(Size(5, 5), 3)
    assert game.state is GameState.NOTSTARTED, puzzle_snapshot(game, "GameState.NOTSTARTED")
    assert game.t_start is None, puzzle_snapshot(game, "t_start is None")


@pytest.mark.parametrize(
    ("size", "n_mines"),
    [(Size(9, 9), 10), (Size(16, 16), 40), (Size(30, 16), 99)],
)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_first_click_is_safe(size: Size, n_mines: int, seed: int) -> None:
    random.seed(seed)
    game = Minesweeper(size, n_mines)
    start = Position(size.w // 2, size.h // 2)
    reveal(game, start)
    assert game.board[start].revealed, puzzle_snapshot(
        game, f"first click at {start} should reveal it"
    )
    assert game.state in (GameState.RUNNING, GameState.WON), puzzle_snapshot(
        game, "RUNNING or WON after first click"
    )
    assert game.t_start is not None, puzzle_snapshot(game, "timer should start on first click")


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_first_click_opens_zero_region(seed: int) -> None:
    random.seed(seed)
    game = Minesweeper(Size(9, 9), 10)
    start = Position(4, 4)
    reveal(game, start)
    assert game.board[start].number == 0, puzzle_snapshot(
        game, f"first click at {start} should show 0"
    )
    assert revealed_count(game) >= 9, puzzle_snapshot(
        game, "first click should open the clicked cell and its 8 neighbours"
    )


# --------------------------------------------------------------------------- 第二次点击


def test_second_click_on_frontier_reveals() -> None:
    game = Minesweeper(Size(3, 3), 1)
    reveal_manually(game, {Position(1, 1): 1})
    before = revealed_count(game)
    target = Position(0, 0)
    reveal(game, target)
    assert game.board[target].revealed, puzzle_snapshot(game, f"{target} should be revealed")
    assert revealed_count(game) > before, puzzle_snapshot(
        game, f"revealed count should increase from {before}"
    )
    assert game.state is not GameState.LOST, puzzle_snapshot(game, "should not be LOST")


def test_second_click_on_frontier_reveals_2x2() -> None:
    game = Minesweeper(Size(2, 2), 1)
    reveal_manually(game, {Position(0, 0): 1})
    target = Position(1, 0)
    reveal(game, target)
    assert game.board[target].revealed, puzzle_snapshot(game, f"{target} should be revealed")
    assert game.state is not GameState.LOST, puzzle_snapshot(game, "should not be LOST")


@pytest.mark.parametrize("n_mines", [1, 2, 10])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_frontier_click_never_raises(n_mines: int, seed: int) -> None:
    random.seed(seed)
    game = Minesweeper(Size(9, 9), n_mines)
    reveal(game, Position(4, 4))
    cells = frontier(game)
    if not cells:
        pytest.skip("no frontier")
    try:
        reveal(game, cells[0])
    except AssertionError:
        raise
    assert game.state in (GameState.RUNNING, GameState.WON, GameState.LOST), puzzle_snapshot(
        game, "state should be RUNNING/WON/LOST after a frontier click"
    )


# --------------------------------------------------------------------------- 非前沿必安格


def test_non_frontier_safe_cell_is_revealed() -> None:
    # 1 雷，中心显示 1 → 16 个非前沿格其实全是必安格。
    game = Minesweeper(Size(5, 5), 1)
    reveal_manually(game, {Position(2, 2): 1})
    target = Position(0, 0)
    assert target in non_frontier(game), puzzle_snapshot(
        game, f"{target} should be non-frontier"
    )
    reveal(game, target)
    assert game.board[target].revealed, puzzle_snapshot(game, f"{target} should be revealed")
    assert game.state is not GameState.LOST, puzzle_snapshot(game, "should not be LOST")


def test_winning_non_frontier_move_is_not_overwritten() -> None:
    # 唯一雷在 (1,0) 附近；点远处的必安非前沿格会翻开安全区并直接 WON，
    # 不能被后续的 LOST 分支覆盖（这是老 bug 的回归）。
    random.seed(0)
    game = Minesweeper(Size(4, 4), 1)
    reveal_manually(game, {Position(0, 0): 1, Position(1, 1): 1})
    target = Position(3, 0)
    assert target in non_frontier(game), puzzle_snapshot(
        game, f"{target} should be non-frontier"
    )
    reveal(game, target)
    assert game.board[target].revealed, puzzle_snapshot(game, f"{target} should be revealed")
    assert game.state is GameState.WON, puzzle_snapshot(game, "expected WON, not overwritten by LOST")


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
    target = Position(2, 1)
    reveal(game, target)
    assert game.board[target].revealed, puzzle_snapshot(game, f"{target} should be revealed")
    assert game.state is GameState.RUNNING, puzzle_snapshot(game, "expected RUNNING")


def test_guess_when_safe_move_exists_loses() -> None:
    game = make_3x2()
    target = Position(1, 0)
    reveal(game, target)
    assert game.state is GameState.LOST, puzzle_snapshot(game, "expected LOST")


def test_safe_cell_is_revealed_with_mixed_boundary() -> None:
    game = make_3x3_four_mines()
    target = Position(1, 1)
    reveal(game, target)
    assert game.board[target].revealed, puzzle_snapshot(game, f"{target} should be revealed")
    assert game.state is GameState.RUNNING, puzzle_snapshot(game, "expected RUNNING")


def test_guess_with_safe_move_loses_on_mixed_boundary() -> None:
    game = make_3x3_four_mines()
    target = Position(2, 1)
    reveal(game, target)
    assert game.state is GameState.LOST, puzzle_snapshot(game, "expected LOST")


def test_guess_into_unrevealed_region_loses_when_safe_move_exists() -> None:
    game = Minesweeper(Size(5, 5), 2)
    reveal_manually(game, {Position(0, 0): 0, Position(1, 1): 1})
    target = Position(4, 4)
    assert target in non_frontier(game), puzzle_snapshot(
        game, f"{target} should be non-frontier"
    )
    reveal(game, target)
    assert game.state is GameState.LOST, puzzle_snapshot(game, "expected LOST")


# --------------------------------------------------------------------------- 终局后的点击


def test_click_after_won_is_ignored() -> None:
    game = Minesweeper(Size(5, 5), 3)
    game.state = GameState.WON
    hidden = [pos for pos, cell in game.board.items() if not cell.revealed]
    assert hidden, puzzle_snapshot(game, "expected at least one hidden cell")
    before = revealed_count(game)
    reveal(game, hidden[0])
    assert game.state is GameState.WON, puzzle_snapshot(game, "state should stay WON")
    assert revealed_count(game) == before, puzzle_snapshot(
        game, f"revealed count should stay {before}"
    )


def test_click_after_lost_is_ignored() -> None:
    game = make_3x2()
    game.state = GameState.LOST
    before = revealed_count(game)
    reveal(game, Position(2, 1))
    assert game.state is GameState.LOST, puzzle_snapshot(game, "state should stay LOST")
    assert revealed_count(game) == before, puzzle_snapshot(
        game, f"revealed count should stay {before}"
    )


def test_click_on_revealed_cell_is_ignored() -> None:
    game = Minesweeper(Size(4, 4), 1)
    reveal_manually(game, {Position(1, 1): 1})
    before_state = game.state
    before = revealed_count(game)
    reveal(game, Position(1, 1))
    assert game.state is before_state, puzzle_snapshot(game, f"state should stay {before_state.name}")
    assert revealed_count(game) == before, puzzle_snapshot(
        game, f"revealed count should stay {before}"
    )


def test_click_on_flagged_cell_is_ignored() -> None:
    game = Minesweeper(Size(4, 4), 1)
    reveal_manually(game, {Position(1, 1): 1})
    target = Position(0, 0)
    game.flag_at(target)
    before_state = game.state
    before = revealed_count(game)
    reveal(game, target)
    assert game.state is before_state, puzzle_snapshot(game, f"state should stay {before_state.name}")
    assert revealed_count(game) == before, puzzle_snapshot(
        game, f"revealed count should stay {before}"
    )
    assert not game.board[target].revealed, puzzle_snapshot(game, f"{target} should stay hidden")


def test_check_win_does_not_revive_a_lost_game() -> None:
    game = Minesweeper(Size(5, 5), 2)
    game.state = GameState.LOST
    game.check_win()
    assert game.state is GameState.LOST, puzzle_snapshot(game, "check_win must not change LOST")

    game.state = GameState.WON
    game.check_win()
    assert game.state is GameState.WON, puzzle_snapshot(game, "check_win must not change WON")


# --------------------------------------------------------------------------- 插旗


def test_flag_at_ignored_before_game_starts() -> None:
    game = Minesweeper(Size(5, 5), 3)
    target = Position(1, 1)
    game.flag_at(target)
    assert not game.board[target].flagged, puzzle_snapshot(
        game, f"flag_at({target}) must be ignored before start"
    )


def test_flag_at_ignored_after_game_over() -> None:
    game = Minesweeper(Size(5, 5), 2)
    target = Position(0, 0)

    game.state = GameState.WON
    game.flag_at(target)
    assert not game.board[target].flagged, puzzle_snapshot(
        game, f"flag_at({target}) must be ignored after WON"
    )

    game.state = GameState.LOST
    game.flag_at(target)
    assert not game.board[target].flagged, puzzle_snapshot(
        game, f"flag_at({target}) must be ignored after LOST"
    )


def test_flag_at_toggles_only_on_unrevealed_cells() -> None:
    random.seed(0)
    game = Minesweeper(Size(5, 5), 3)
    reveal(game, Position(2, 2))

    revealed = Position(2, 2)
    game.flag_at(revealed)
    assert not game.board[revealed].flagged, puzzle_snapshot(
        game, f"flag_at({revealed}) must be ignored on a revealed cell"
    )

    target = next(pos for pos, cell in game.board.items() if not cell.revealed)
    game.flag_at(target)
    assert game.board[target].flagged, puzzle_snapshot(game, f"{target} should be flagged")
    game.flag_at(target)
    assert not game.board[target].flagged, puzzle_snapshot(
        game, f"{target} should be unflagged by the second flag_at"
    )


def test_flagged_cell_blocks_reveal_until_unflagged() -> None:
    # (0,0)=0 让 (0,1) 成为必安格，取消插旗后再点击不会按“猜”判负。
    game = Minesweeper(Size(3, 3), 1)
    reveal_manually(game, {Position(0, 0): 0})
    target = Position(0, 1)

    game.flag_at(target)
    assert game.board[target].flagged, puzzle_snapshot(game, f"{target} should be flagged")
    reveal(game, target)
    assert not game.board[target].revealed, puzzle_snapshot(
        game, f"{target} must stay hidden while flagged"
    )
    assert game.state is GameState.RUNNING, puzzle_snapshot(game, "state should stay RUNNING")

    game.flag_at(target)
    assert not game.board[target].flagged, puzzle_snapshot(game, f"{target} should be unflagged")
    reveal(game, target)
    assert game.board[target].revealed, puzzle_snapshot(game, f"{target} should be revealed")
    assert game.state is not GameState.LOST, puzzle_snapshot(game, "should not be LOST")


# --------------------------------------------------------------------------- chord


def test_clicking_revealed_number_without_matching_flags_is_noop() -> None:
    game = Minesweeper(Size(3, 3), 1)
    reveal_manually(game, {Position(1, 1): 1})
    center = Position(1, 1)
    assert game.board[center].number == 1, puzzle_snapshot(game, f"{center} should show 1")

    before = revealed_count(game)
    reveal(game, center)  # 没有旗 -> 不触发 chord
    assert game.state is GameState.RUNNING, puzzle_snapshot(game, "expected RUNNING")
    assert revealed_count(game) == before, puzzle_snapshot(
        game, f"revealed count should stay {before}"
    )

    for target in (Position(0, 0), Position(2, 2)):
        game.flag_at(target)  # 2 面旗 != 数字 1 -> 不触发 chord
    before = revealed_count(game)
    reveal(game, center)
    assert game.state is GameState.RUNNING, puzzle_snapshot(game, "expected RUNNING")
    assert revealed_count(game) == before, puzzle_snapshot(
        game, f"revealed count should stay {before}"
    )


def test_chord_batch_behaves_like_manual_clicks() -> None:
    random.seed(0)
    game = Minesweeper(Size(3, 3), 1)
    reveal_manually(game, {Position(1, 1): 1})
    center = Position(1, 1)
    assert game.board[center].number == 1, puzzle_snapshot(game, f"{center} should show 1")
    game.flag_at(Position(0, 0))  # 1 面旗 == 数字 1 -> 点击数字触发 chord

    before = revealed_count(game)
    reveal(game, center)
    # chord 就是依次手动点击未插旗邻居：先翻出信息，随后某次点击变成“有必安格还猜”
    assert game.state is GameState.LOST, puzzle_snapshot(game, "expected LOST")
    assert revealed_count(game) > before, puzzle_snapshot(
        game, f"chord should reveal at least one cell before losing (before={before})"
    )


def test_chord_reveals_provably_safe_neighbours() -> None:
    game = Minesweeper(Size(3, 3), 1)
    reveal_manually(game, {Position(0, 0): 1, Position(2, 0): 0})
    game.flag_at(Position(0, 1))

    reveal(game, Position(0, 0))

    for target in (Position(1, 0), Position(1, 1)):
        assert game.board[target].revealed, puzzle_snapshot(
            game, f"{target} should be revealed by chord"
        )
    assert game.state is GameState.RUNNING, puzzle_snapshot(game, "expected RUNNING")


def test_chord_does_not_recurse_between_adjacent_revealed_numbers() -> None:
    game = Minesweeper(Size(2, 2), 1)
    reveal_manually(game, {Position(0, 0): 1, Position(1, 0): 1})
    game.flag_at(Position(0, 1))

    reveal(game, Position(0, 0))  # 不能回弹到已翻开的 (1,0) 再触发一次 chord

    assert game.state is GameState.WON, puzzle_snapshot(game, "expected WON, not recursion")
    assert game.board[Position(1, 1)].revealed, puzzle_snapshot(
        game, "(1,1) should be revealed"
    )


def test_chord_with_no_unrevealed_neighbour_is_noop() -> None:
    game = Minesweeper(Size(3, 3), 8)
    reveal_manually(game, {Position(1, 1): 8})
    for target in game.enum_adj(Position(1, 1)):
        game.flag_at(target)

    before_state = game.state
    before = revealed_count(game)
    reveal(game, Position(1, 1))
    assert game.state is before_state, puzzle_snapshot(
        game, f"state should stay {before_state.name}"
    )
    assert revealed_count(game) == before, puzzle_snapshot(
        game, f"revealed count should stay {before}"
    )
