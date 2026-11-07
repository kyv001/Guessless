# -*- AI Generated -*-
"""rules.md（常规模式）的可执行版本。

运行：
    uv run pytest -q tests/test_rules.py
    # 或者：.venv/bin/pytest -q tests/test_rules.py

规则覆盖（与 rules.md 一一对应）：
    可点击范围
        1.1 未开局任意格 ................ test_rule_1_1_any_cell_is_clickable_before_start
        1.2 一定安全的沿岸/内陆格 ....... test_rule_1_2_must_safe_frontier_cell_is_clickable
                                          test_rule_1_2_must_safe_inland_cell_is_clickable
        1.3 无一定安全格时 .............. test_rule_1_3_uncertain_frontier_cell_is_clickable_without_safe_cells
                                          test_rule_1_3_uncertain_inland_cell_is_clickable_without_safe_cells
                                          test_rule_1_3_uncertain_inland_cell_is_clickable_even_when_all_frontier_cells_are_mines
    不可点击范围
        2.1 一定危险的格子 .............. test_rule_2_1_must_mine_cell_is_not_clickable
        2.2 存在安全格时的不确定格 ...... test_rule_2_2_uncertain_frontier_cell_is_not_clickable_while_safe_cell_exists
                                          test_rule_2_2_uncertain_inland_cell_is_not_clickable_while_safe_cell_exists
    定义
        1 可行配置 ...................... test_definition_1_feasible_configurations
        2/3/4 三类格子 .................. test_definitions_2_3_4_classification
        5/6 沿岸/内陆 ................... test_definitions_5_6_frontier_and_inland
    游戏流程
        1 初始全部未展开 ................ test_flow_1_board_starts_fully_hidden
        2 首击 3x3 无雷 ................. test_flow_2_first_click_3x3_has_no_mines
        3 点到雷判负 .................... test_flow_3_clicking_a_mine_loses
        4 剩余数等于雷数获胜 ............ test_flow_4_win_when_hidden_count_equals_mine_count

所有局面都是测试内手工搭建的确定性小局面；只有首击相关用例固定随机种子。
"""

import random

import pytest

from guessless.core import Cell, GameState, Minesweeper, Position, Size

MUST_SAFE = "一定安全"
MUST_MINE = "一定危险"
UNCERTAIN = "不一定不安全"


# ---------------------------------------------------------------- 场景与规则定义


def game_from(drawing: str, n_mines: int) -> Minesweeper:
    """按 ASCII 画局面构造 RUNNING 状态（仅供测试）。

    - 每个 token 一格：按行对应 y，按列对应 x；
    - 数字 0-8：已翻开且显示该数字；
    - ``#``：未翻开。
    """
    rows = [line.split() for line in drawing.strip().splitlines() if line.strip()]
    if not rows:
        raise ValueError("空局面")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("每行格数必须一致")

    game = Minesweeper(Size(width, len(rows)), n_mines)
    game.state = GameState.RUNNING
    for y, row in enumerate(rows):
        for x, token in enumerate(row):
            if token == "#":
                continue
            if not (token.isdigit() and len(token) == 1 and 0 <= int(token) <= 8):
                raise ValueError(f"无法识别的格子 {token!r}")
            cell = game.board[Position(x, y)]
            cell.revealed = True
            cell.number = int(token)
    return game


def frontier(game: Minesweeper) -> set[Position]:
    """定义 5：未翻开且与至少一个已翻开格相邻的格子。"""
    return {
        pos
        for pos, cell in game.board.items()
        if not cell.revealed and any(game.get_at(adj).revealed for adj in game.enum_adj(pos))
    }


def inland(game: Minesweeper) -> set[Position]:
    """定义 6：未翻开且不与任何已翻开格相邻的格子。"""
    return {pos for pos, cell in game.board.items() if not cell.revealed} - frontier(game)


def unrevealed(game: Minesweeper) -> set[Position]:
    return {pos for pos, cell in game.board.items() if not cell.revealed}


def classify(game: Minesweeper, position: Position) -> str:
    """按定义 2/3/4，只从可行配置（solve().solutions）推导格子类别。

    不走 SolveResult 里现成的 must_be_*/cannot_deter 字段，避免测试用实现
    的结论去验证实现本身。
    """
    result = game.solve()
    if result is None or not result.solutions:
        raise AssertionError(f"局面没有可行配置，无法分类 {position}\n{render(game)}")

    # 每个解给出“position 能不能是雷 / 能不能安全”两个谓词，再对解集量化：
    # 所有解都不允许是雷 → 一定安全；所有解都不允许安全 → 一定危险；否则不一定不安全。
    # 内陆格是否可安全/可为雷取决于该解还有多少颗未定位的雷，不能只看 mine_positions。
    inner = inland(game)

    def can_be_mine(solution) -> bool:
        return position in solution.mine_positions or (
            position in inner and solution.n_extra_mines != 0
        )

    def can_be_safe(solution) -> bool:
        return position not in solution.mine_positions and (
            position not in inner or solution.n_extra_mines <= len(inner) - 1
        )

    if not any(can_be_mine(solution) for solution in result.solutions):
        return MUST_SAFE
    if not any(can_be_safe(solution) for solution in result.solutions):
        return MUST_MINE
    return UNCERTAIN


def cells_of(game: Minesweeper, kind: str) -> set[Position]:
    """取某类未翻开格子的集合。"""
    return {pos for pos in unrevealed(game) if classify(game, pos) == kind}


def render(game: Minesweeper) -> str:
    """失败信息里的棋盘快照。"""

    def symbol(cell: Cell) -> str:
        if cell.revealed:
            return str(cell.number) if cell.number else "."
        return "F" if cell.flagged else "#"

    lines = [
        f"state={game.state.name} n_mines={game.n_mines} hidden={len(unrevealed(game))}",
    ]
    for y in range(game.size.h):
        row = " ".join(f"{symbol(game.get_at(Position(x, y))):>2}" for x in range(game.size.w))
        lines.append(f"y={y:<2} {row}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 断言 helper


def assert_classified(game: Minesweeper, position: Position, kind: str) -> None:
    actual = classify(game, position)
    assert actual == kind, f"{position} 应为「{kind}」，实际「{actual}」\n{render(game)}"


def _click(game: Minesweeper, position: Position) -> None:
    try:
        game.reveal_at(position)
    except Exception as exc:  # noqa: BLE001 - 失败信息需要带棋盘快照
        raise AssertionError(f"点击 {position} 抛出 {type(exc).__name__}: {exc}\n{render(game)}") from exc


def assert_click_allowed(game: Minesweeper, position: Position) -> None:
    _click(game, position)
    assert game.state is not GameState.LOST, f"规则判定 {position} 可点，却判负\n{render(game)}"
    assert game.get_at(position).revealed, f"规则判定 {position} 可点，却没有翻开\n{render(game)}"


def assert_click_forbidden(game: Minesweeper, position: Position) -> None:
    _click(game, position)
    assert game.state is GameState.LOST, (
        f"规则判定 {position} 不可点，实际 state={game.state.name}\n{render(game)}"
    )
    assert not game.get_at(position).revealed, f"判负时 {position} 不应被翻开\n{render(game)}"


# ---------------------------------------------------------------- 可点击范围


def test_rule_1_1_any_cell_is_clickable_before_start() -> None:
    """可点击范围 1：未开局时的任意格子。"""
    random.seed(0)  # 固定首击雷区序列，失败可复现
    for y in range(4):
        for x in range(4):
            position = Position(x, y)
            game = Minesweeper(Size(4, 4), 3)
            assert game.state is GameState.NOTSTARTED
            assert_click_allowed(game, position)


def test_rule_1_2_must_safe_frontier_cell_is_clickable() -> None:
    """可点击范围 2：证明一定安全的沿岸格子。

    1×3，左侧显示 0：中间沿岸格必安，唯一的雷只能落在右侧内陆格。
    """
    game = game_from("0 # #", n_mines=1)
    target = Position(1, 0)
    assert target in frontier(game), f"{target} 应当是沿岸格\n{render(game)}"
    assert_classified(game, target, MUST_SAFE)
    assert_click_allowed(game, target)


def test_rule_1_2_must_safe_inland_cell_is_clickable() -> None:
    """可点击范围 2：证明一定安全的内陆格子（3×3 左上角显示 1）。"""
    game = game_from(
        """
        1 # #
        # # #
        # # #
        """,
        n_mines=1,
    )
    target = Position(2, 2)
    assert target in inland(game), f"{target} 应当是内陆格\n{render(game)}"
    assert_classified(game, target, MUST_SAFE)
    assert_click_allowed(game, target)


def test_rule_1_3_uncertain_frontier_cell_is_clickable_without_safe_cells() -> None:
    """可点击范围 3：无一定安全格时，不一定不安全的沿岸格可点。

    3×3，左上角显示 1，共 2 雷：沿岸 3 格共 1 雷，内陆 5 格共 1 雷，
    因此没有必安格，但沿岸格可以猜。
    """
    game = game_from(
        """
        1 # #
        # # #
        # # #
        """,
        n_mines=2,
    )
    target = Position(1, 0)
    safe = cells_of(game, MUST_SAFE)
    assert not safe, f"本场景不应有「{MUST_SAFE}」格，实际 {safe}\n{render(game)}"
    assert target in frontier(game), f"{target} 应当是沿岸格\n{render(game)}"
    assert_classified(game, target, UNCERTAIN)
    assert_click_allowed(game, target)


def test_rule_1_3_uncertain_inland_cell_is_clickable_even_when_all_frontier_cells_are_mines() -> None:
    """可点击范围 3：即使沿岸全不安全，不一定不安全的内陆格也可点。

    4×4，(1,1) 显示 8，共 10 雷：8 个沿岸格全是雷，剩余 2 雷在 7 个内陆格中。
    """
    game = game_from(
        """
        # # # #
        # 8 # #
        # # # #
        # # # #
        """,
        n_mines=10,
    )
    target = Position(3, 3)
    safe = cells_of(game, MUST_SAFE)
    assert not safe, f"本场景不应有「{MUST_SAFE}」格，实际 {safe}\n{render(game)}"
    mines = cells_of(game, MUST_MINE)
    assert frontier(game) <= mines, f"所有沿岸格都应是「{MUST_MINE}」\n{render(game)}"
    assert target in inland(game), f"{target} 应当是内陆格\n{render(game)}"
    assert_classified(game, target, UNCERTAIN)
    assert_click_allowed(game, target)


def test_rule_1_3_uncertain_inland_cell_is_clickable_without_safe_cells() -> None:
    """可点击范围 3：无一定安全格时，不一定不安全的内陆格也可点。

    3×3，左上角显示 1，共 2 雷：沿岸 3 格不确定，内陆 5 格也不确定；
    没有必安格，沿岸和内陆都可以猜。
    """
    game = game_from(
        """
        1 # #
        # # #
        # # #
        """,
        n_mines=2,
    )
    target = Position(2, 2)
    safe = cells_of(game, MUST_SAFE)
    assert not safe, f"本场景不应有「{MUST_SAFE}」格，实际 {safe}\n{render(game)}"
    assert target in inland(game), f"{target} 应当是内陆格\n{render(game)}"
    assert_classified(game, target, UNCERTAIN)
    assert_click_allowed(game, target)


# ---------------------------------------------------------------- 不可点击范围


def test_rule_2_1_must_mine_cell_is_not_clickable() -> None:
    """不可点击范围 1：一定危险的格子（沿岸与内陆两种）。"""
    # 沿岸必雷：4×4，(1,1) 显示 8，共 10 雷。
    game = game_from(
        """
        # # # #
        # 8 # #
        # # # #
        # # # #
        """,
        n_mines=10,
    )
    frontier_mine = Position(0, 0)
    assert frontier_mine in frontier(game)
    assert_classified(game, frontier_mine, MUST_MINE)
    assert_click_forbidden(game, frontier_mine)

    # 内陆必雷：3×3 混合数字，剩余 1 雷只能落在内陆 (2,0)。
    game = game_from(
        """
        1 # #
        2 # #
        # 2 #
        """,
        n_mines=4,
    )
    inland_mine = Position(2, 0)
    assert inland_mine in inland(game)
    assert_classified(game, inland_mine, MUST_MINE)
    assert_click_forbidden(game, inland_mine)


def test_rule_2_2_uncertain_frontier_cell_is_not_clickable_while_safe_cell_exists() -> None:
    """不可点击范围 2：存在安全格时，沿岸的不确定格不可点。"""
    game = game_from(
        """
        1 # #
        # # #
        # # #
        """,
        n_mines=1,
    )
    target = Position(1, 0)
    safe = cells_of(game, MUST_SAFE)
    assert safe, f"本场景应存在「{MUST_SAFE}」格\n{render(game)}"
    assert target in frontier(game)
    assert_classified(game, target, UNCERTAIN)
    assert_click_forbidden(game, target)


def test_rule_2_2_uncertain_inland_cell_is_not_clickable_while_safe_cell_exists() -> None:
    """不可点击范围 2：存在安全格时，内陆的不确定格也不可点。

    5×5，(0,0) 显示 0、(1,1) 显示 1，共 2 雷：有必安格，也有不确定内陆格。
    """
    game = game_from(
        """
        0 # # # #
        # 1 # # #
        # # # # #
        # # # # #
        # # # # #
        """,
        n_mines=2,
    )
    target = Position(4, 4)
    safe = cells_of(game, MUST_SAFE)
    assert safe, f"本场景应存在「{MUST_SAFE}」格\n{render(game)}"
    assert target in inland(game), f"{target} 应当是内陆格\n{render(game)}"
    assert_classified(game, target, UNCERTAIN)
    assert_click_forbidden(game, target)


# ---------------------------------------------------------------- 定义


def test_definition_1_feasible_configurations() -> None:
    """定义 1：可行配置 = 与当前局面一致的所有雷分布。"""
    game = game_from("# # 1", n_mines=1)
    result = game.solve()
    assert result is not None
    feasible = [(set(solution.mine_positions), solution.n_extra_mines) for solution in result.solutions]
    assert feasible == [({Position(1, 0)}, 0)], f"可行配置应只有中间一格有雷\n{render(game)}"


def test_definitions_2_3_4_classification() -> None:
    """定义 2/3/4：一定安全 / 一定危险 / 不一定不安全。

    3×3 混合数字，共 4 雷：
    - (1,1) 在所有可行配置中都安全；
    - (1,0)、(0,2) 在沿岸中必雷，(2,0) 是内陆必雷（剩余 1 雷无处可去）；
    - (2,1)、(2,2) 是沿岸不确定格。
    """
    game = game_from(
        """
        1 # #
        2 # #
        # 2 #
        """,
        n_mines=4,
    )
    assert cells_of(game, MUST_SAFE) == {Position(1, 1)}, render(game)
    assert cells_of(game, MUST_MINE) == {Position(1, 0), Position(0, 2), Position(2, 0)}, render(game)
    assert cells_of(game, UNCERTAIN) == {Position(2, 1), Position(2, 2)}, render(game)


def test_definitions_5_6_frontier_and_inland() -> None:
    """定义 5/6：沿岸格 = 与已翻开格相邻；内陆格 = 其余未翻开格。"""
    game = game_from(
        """
        1 # #
        # # #
        # # #
        """,
        n_mines=1,
    )
    assert frontier(game) == {Position(1, 0), Position(0, 1), Position(1, 1)}
    assert inland(game) == {
        Position(2, 0),
        Position(2, 1),
        Position(2, 2),
        Position(0, 2),
        Position(1, 2),
    }


# ---------------------------------------------------------------- 游戏流程


def test_flow_1_board_starts_fully_hidden() -> None:
    """游戏流程 1：游戏开始时所有格子均未展开。"""
    game = Minesweeper(Size(4, 4), 3)
    assert game.state is GameState.NOTSTARTED
    assert unrevealed(game) == set(game.board), render(game)
    assert not any(cell.flagged for cell in game.board.values()), render(game)


@pytest.mark.parametrize(
    "position",
    [Position(0, 0), Position(0, 4), Position(4, 4)],
    ids=["corner", "edge", "center"],
)
def test_flow_2_first_click_3x3_has_no_mines(position: Position) -> None:
    """游戏流程 2：第一次点击附近 3×3 必定没有地雷。"""
    random.seed(0)
    game = Minesweeper(Size(9, 9), 10)
    assert_click_allowed(game, position)
    assert game.get_at(position).number == 0, (
        f"首击格应显示 0（3×3 无雷），实际 {game.get_at(position).number}\n{render(game)}"
    )
    zone = [position, *game.enum_adj(position)]
    for pos in zone:
        assert game.get_at(pos).revealed, f"{pos} 在首击 3×3 内，应当被翻开\n{render(game)}"


def test_flow_3_clicking_a_mine_loses() -> None:
    """游戏流程 3：点到地雷则游戏失败。

    reveal_at 对不确定格的“猜”会选一个让点击格安全的相容局面；
    因此用户可见的“点到雷”就是点到一定危险格（与不可点击范围 1 一致）。
    """
    game = game_from("# # 1", n_mines=1)
    target = Position(1, 0)
    assert_classified(game, target, MUST_MINE)
    assert_click_forbidden(game, target)


def test_flow_4_win_when_hidden_count_equals_mine_count() -> None:
    """游戏流程 4：未展开的格子数量等于总地雷数量时获胜。"""
    game = game_from("# # 1", n_mines=1)
    assert len(unrevealed(game)) > game.n_mines, render(game)
    assert_click_allowed(game, Position(0, 0))
    assert game.state is GameState.WON, f"剩余 1 格 1 雷时应获胜\n{render(game)}"
    assert len(unrevealed(game)) == game.n_mines, render(game)
