# -*- AI Generated -*-
"""Guessless 的 Tk 图形界面。

保持参考实现 (playground/minesweeper_tk.py) 的配色与触摸手感，并补充：
窗口化默认、实时计时、持久化排行榜、昵称输入、多窗口与响应式布局。

关键约束：
- 引擎的 ``reveal_at`` 在困难棋盘上可能阻塞数秒，因此一律放到 daemon 线程执行，
  主线程只用 ``after`` 轮询结果，绝不读取正在被修改的棋盘；
- 只依赖标准库与 tkinter，不引入第三方库。
"""

from __future__ import annotations

import argparse
import ctypes
import math
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox

# 作为脚本直接运行（python src/guessless/ui.py）时补上 src 目录，改用绝对导入
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from guessless.core import GameState, Minesweeper, Position, Size, SolveResult
    from guessless.leaderboard import DEFAULT_NAME, Entry, Leaderboard, sanitize_name
else:
    from .core import GameState, Minesweeper, Position, Size, SolveResult
    from .leaderboard import DEFAULT_NAME, Entry, Leaderboard, sanitize_name

type WorkerResult = tuple[int, str, str | None, float | None, object]

# ---- 配色（与参考实现一致） -----------------------------------------
BG, BAR, CHIP = "#263238", "#1c262b", "#2f3e46"
HIDDEN, HIDDEN_EDGE = "#90a4ae", "#607d8b"
REVEALED, GRID, PRESS = "#eceff1", "#b0bec5", "#b0bec5"
FLAG, MINE, WRONG, MUTED = "#d32f2f", "#212121", "#c62828", "#90a4ae"
ACCENT, ACCENT_HOVER = "#1565c0", "#1e88e5"
DANGER, DANGER_HOVER = "#c62828", "#e53935"
NEUTRAL, NEUTRAL_HOVER = "#37474f", "#546e7a"
WIN_COLOR, WIN_SUB, LOSE_COLOR = "#43a047", "#81c784", "#e53935"
ROW_HIGHLIGHT = "#1565c0"
NUM_COLORS = {1: "#1976d2", 2: "#2e7d32", 3: "#d32f2f", 4: "#6a1b9a",
              5: "#ef6c00", 6: "#00838f", 7: "#37474f", 8: "#000000"}
# 爆炸复盘：安全 / 必雷 / 不确定 三色填色
MARKER_FILLS = {"safe": "#66bb6a", "mine": "#ef5350", "uncertain": "#ffca28"}
MARKER_EDGES = {"safe": "#1b5e20", "mine": "#b71c1c", "uncertain": "#e65100"}
EXPLODED_FILL, EXPLODED_EDGE = "#b71c1c", "#4a0000"
HINT_PENALTY = 5.0  # 每次提示的罚时（秒）
HINT_COLOR, HINT_HOVER = "#ef6c00", "#f57c00"  # 提示生效时的按钮底色
REASON_TEXT = {
    "must_be_mine": "点到了必雷格",
    "safe_move_exists": "存在安全格，却点了非安全格",
    "must_guess_frontier": "前沿还有不确定格，却点了内陆格",
}

LONG_PRESS = 0.45  # 触摸长按判定阈值（秒）
THINKING = ("·", "··", "···")
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


@dataclass(frozen=True)
class Difficulty:
    """一种难度：排行榜键、显示名、棋盘尺寸与雷数。"""

    key: str
    label: str
    cols: int
    rows: int
    mines: int


DIFFICULTIES = (
    Difficulty("easy", "Easy", 9, 9, 10),
    Difficulty("medium", "Mid", 16, 16, 40),
    Difficulty("hard", "Hard", 30, 16, 99),
)
DIFFICULTY_BY_KEY = {difficulty.key: difficulty for difficulty in DIFFICULTIES}


# ---- 爆炸复盘：把引擎的推理快照翻译成界面数据 -------------------------
@dataclass(frozen=True)
class ConstraintInfo:
    """爆炸格相邻的一条线性限制：某个已翻开数字要求它的暗邻中有多少雷。"""

    position: Position  # 给出该限制的数字格
    number: int
    hidden: int  # 该数字周围仍未翻开的格子数
    decisive: bool  # number == hidden：暗邻必须全是雷，单条即可判定


@dataclass(frozen=True)
class LossAnalysis:
    """爆炸瞬间的推理快照，供复盘界面显示。"""

    position: Position  # 实际触发爆炸的格子
    reason: str  # must_be_mine / safe_move_exists / must_guess_frontier / unknown
    labels: dict[Position, str]  # 每个暗格 -> safe / mine / uncertain
    safe_count: int
    mine_count: int
    uncertain_count: int
    solutions: int  # 与已知数字一致的盘面数
    total_mines: int
    constraints: tuple[ConstraintInfo, ...]
    decisive: tuple[ConstraintInfo, ...]
    highlight: tuple[Position, ...]  # 需要描边的数字格


class AnalysedMinesweeper(Minesweeper):
    """记录每次求解结果，仅用于爆炸复盘，不改变任何游戏规则。"""

    def __init__(self, size: Size, n_mines: int) -> None:
        super().__init__(size, n_mines)
        self.last_decision: tuple[Position, SolveResult | None] | None = None
        self._reveal_position: Position | None = None

    def reveal_at(self, position: Position) -> None:
        self._reveal_position = position
        super().reveal_at(position)

    def solve(self) -> SolveResult | None:
        result = super().solve()
        if self._reveal_position is not None:
            self.last_decision = (self._reveal_position, result)
        return result


def loss_reason(result: SolveResult, position: Position) -> str:
    """复刻 reveal_at 的判定顺序，说明这次点击栽在哪条规则上。"""
    if position in set(result.must_be_mine):
        return "must_be_mine"
    if result.must_be_safe:
        return "unknown" if position in set(result.must_be_safe) else "safe_move_exists"
    if result.cannot_deter and position not in set(result.cannot_deter):
        return "must_guess_frontier"
    return "unknown"


def classify_hidden(game: Minesweeper, result: SolveResult) -> dict[Position, str]:
    """把所有仍未翻开的格子分成 安全 / 必雷 / 不确定 三类。"""
    must_mine, must_safe = set(result.must_be_mine), set(result.must_be_safe)
    cannot_deter, inland = set(result.cannot_deter), set(result.unsolved_positions)
    extras = [solution.n_extra_mines for solution in result.solutions]
    inland_safe = bool(extras) and max(extras) == 0
    inland_mine = bool(extras) and bool(inland) and min(extras) == len(inland)
    labels: dict[Position, str] = {}
    for position, cell in game.board.items():
        if cell.revealed:
            continue
        if position in must_mine:
            labels[position] = "mine"
        elif position in must_safe:
            labels[position] = "safe"
        elif position in cannot_deter:
            labels[position] = "uncertain"
        elif position in inland:
            labels[position] = "safe" if inland_safe else "mine" if inland_mine else "uncertain"
        else:
            labels[position] = "uncertain"
    return labels


def constraint_infos(game: Minesweeper, position: Position) -> tuple[ConstraintInfo, ...]:
    """列出爆炸格相邻数字给出的限制，决定性的排在前面。"""
    infos: list[ConstraintInfo] = []
    for neighbour in game.enum_adj(position):
        cell = game.get_at(neighbour)
        if not cell.revealed:
            continue
        hidden = sum(1 for pos in game.enum_adj(neighbour) if not game.get_at(pos).revealed)
        infos.append(ConstraintInfo(neighbour, cell.number, hidden,
                                    hidden > 0 and cell.number == hidden))
    infos.sort(key=lambda info: (not info.decisive, info.position.y, info.position.x))
    return tuple(infos)


def analyse_loss(game: Minesweeper,
                 decision: tuple[Position, SolveResult | None] | None) -> LossAnalysis | None:
    """用引擎记录的决策快照生成复盘数据；快照不完整时返回 None。"""
    if decision is None:
        return None
    position, result = decision
    if result is None or not result.solutions:
        return None
    reason = loss_reason(result, position)
    labels = classify_hidden(game, result)
    constraints = constraint_infos(game, position) if reason == "must_be_mine" else ()
    decisive = tuple(info for info in constraints if info.decisive)
    highlight = decisive if decisive else constraints
    return LossAnalysis(
        position=position,
        reason=reason,
        labels=labels,
        safe_count=sum(value == "safe" for value in labels.values()),
        mine_count=sum(value == "mine" for value in labels.values()),
        uncertain_count=sum(value == "uncertain" for value in labels.values()),
        solutions=len(result.solutions),
        total_mines=game.n_mines,
        constraints=constraints,
        decisive=decisive,
        highlight=tuple(info.position for info in highlight),
    )


def describe_loss(analysis: LossAnalysis) -> tuple[str, str]:
    """生成一句原因和一句限制说明（给复盘卡片用）。"""
    reason = REASON_TEXT.get(analysis.reason, "触发了限制")
    if analysis.reason == "must_be_mine":
        if analysis.decisive or analysis.constraints:
            chosen = analysis.decisive or analysis.constraints
            numbers = "、".join(
                f"{info.number}@({info.position.x + 1},{info.position.y + 1})"
                for info in chosen[:2])
            if len(chosen) > 2:
                numbers += " 等"
            if analysis.decisive:
                limit = f"限制：高亮数字 {numbers} 的暗邻全为雷 → 该格必雷"
            else:
                limit = f"限制：高亮数字 {numbers} 与总雷数 {analysis.total_mines} 联立 → 该格必雷"
        else:
            limit = "限制：由总雷数决定"
    elif analysis.reason == "safe_move_exists":
        limit = f"规则：有安全格时必须点安全格（当时 {analysis.safe_count} 个）"
    elif analysis.reason == "must_guess_frontier":
        limit = "规则：前沿还有不确定格时，只能在前沿猜"
    else:
        limit = "限制：未知"
    return reason, limit


# ---- 纯函数工具 -------------------------------------------------------
def format_time(seconds: float | int | None) -> str:
    """把秒数格式化为 M:SS（不足一小时）或 H:MM:SS。"""
    try:
        value = float(seconds or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value) or value < 0:
        value = 0.0
    total = int(value)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def board_metrics(canvas_w: int, canvas_h: int, cols: int, rows: int, pad: int,
                  min_cell: int = 12, max_cell: int = 120) -> tuple[int, int, int]:
    """计算格子边长与居中偏移 (cell, ox, oy)；画布过小时也返回正数格子。"""
    width, height = max(0, int(canvas_w)), max(0, int(canvas_h))
    if cols <= 0 or rows <= 0:
        return (max(1, int(min_cell)), 0, 0)
    padding = max(0, int(pad))
    available_w = max(1, width - 2 * padding)
    available_h = max(1, height - 2 * padding)
    cell = min(available_w // cols, available_h // rows)
    cell = max(int(min_cell), min(int(max_cell), cell))
    return (cell, (width - cell * cols) // 2, (height - cell * rows) // 2)


def format_date(when: object) -> str:
    """把 epoch 秒 / ISO 字符串 / 时间对象转成 MM-DD 短日期（容错）。"""
    if when is None:
        return "-"
    if isinstance(when, (int, float)):
        try:
            stamp = float(when)
            return time.strftime("%m-%d", time.localtime(stamp)) if stamp > 0 else "-"
        except (OverflowError, OSError, ValueError):
            return "-"
    if isinstance(when, str):
        text = when.strip()
        if not text:
            return "-"
        return text[5:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else text[:10]
    formatter = getattr(when, "strftime", None)
    try:
        return str(formatter("%m-%d")) if formatter is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _enable_dpi_awareness() -> None:
    """Windows 高 DPI 适配；必须在创建 Tk 根窗口之前调用。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:
        pass


def _child_python() -> str:
    """子窗口解释器：Windows 优先同级 pythonw.exe，避免弹出控制台。"""
    if sys.platform == "win32":
        sibling = Path(sys.executable).with_name("pythonw.exe")
        if sibling.exists():
            return str(sibling)
    return sys.executable


def build_child_command(difficulty: str, argv0: str | None = None) -> list[str]:
    """构造拉起独立子窗口的命令；优先复用当前 .py 脚本路径。"""
    python = _child_python()
    script = argv0 if argv0 is not None else (sys.argv[0] if sys.argv else "")
    if script.endswith(".py") and Path(script).is_file():
        return [python, script, "--difficulty", difficulty]
    return [python, "-m", "guessless.ui", "--difficulty", difficulty]


class GuesslessApp:
    """单个游戏窗口；每个窗口拥有独立的游戏、排行榜实例与子进程。"""

    def __init__(self, root: tk.Tk, *, difficulty: str = "medium", name: str | None = None,
                 geometry: tuple[int, int] | None = None) -> None:
        self.root = root
        self._closed = False
        try:
            dpi = float(root.winfo_fpixels("1i"))
        except (tk.TclError, ValueError):
            dpi = 96.0
        self.scale = max(1.0, dpi / 96.0)  # 像素常量缩放；字体仍用 point
        self._ui_family = tkfont.nametofont("TkDefaultFont", root=root).actual("family") or "Sans"
        self._mono_family = tkfont.nametofont("TkFixedFont", root=root).actual("family") or "Courier"
        self.difficulty = DIFFICULTY_BY_KEY.get(difficulty, DIFFICULTIES[1])
        self.game: Minesweeper | None = None
        self.flag_mode = self.over = self.won = self.lost = False
        self.busy = False
        self.final_elapsed: float | None = None
        self.lost_cell: Position | None = None
        self.banner: tuple[str, str, str | None] | None = None
        self.analysis: LossAnalysis | None = None
        self._card_visible = False
        self.hint_labels: dict[Position, str] | None = None
        self.hint_active = False
        self.time_penalty = 0.0
        self._revealed_before = 0
        self.epoch = self._cell = self._ox = self._oy = 0
        self._press_active = self._chip_error = self._pending_redraw = False
        self._press_cell: Position | None = None
        self._press_time = self._thinking_index = 0.0
        self._active_click: Position | None = None
        self._after_timer: str | None = None
        self._after_redraw: str | None = None
        self._after_poll: str | None = None
        self._after_thinking: str | None = None
        self._after_name: str | None = None
        self._rank_dialog: RankDialog | None = None
        self._results: queue.SimpleQueue[WorkerResult] = queue.SimpleQueue()
        self._children: list[subprocess.Popen] = []  # 子窗口句柄，用于定期回收僵尸进程
        self.lb: Leaderboard | None
        try:
            self.lb = Leaderboard()
        except Exception:
            traceback.print_exc()
            self.lb = None
        initial = sanitize_name(name) if name else self._remembered_name()
        self._name_var = tk.StringVar(root, value=initial)
        self._build_ui()
        self.new_game()
        self._apply_window(geometry)
        self._after_timer = root.after(150, self._tick_timer)

    @property
    def ui_family(self) -> str:
        return self._ui_family

    @property
    def mono_family(self) -> str:
        return self._mono_family

    def _px(self, value: float) -> int:
        """逻辑像素转物理像素。"""
        return int(round(value * self.scale))

    # ---- 界面搭建 ----------------------------------------------------
    def _make_button(self, parent: tk.Misc, text: str, command: object,
                     bg: str = NEUTRAL, active: str = NEUTRAL_HOVER) -> tk.Button:
        return tk.Button(parent, text=text, command=command, font=(self._ui_family, 12, "bold"),
                         bg=bg, fg="white", activebackground=active, activeforeground="white",
                         relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                         padx=self._px(12), pady=self._px(10))

    def _make_chip(self, parent: tk.Misc, caption: str, font: tkfont.Font, width: int) -> tk.Label:
        frame = tk.Frame(parent, bg=CHIP, padx=self._px(9), pady=self._px(3))
        frame.pack(side="left", padx=(0, self._px(8)))
        tk.Label(frame, text=caption, font=(self._ui_family, 7, "bold"), fg=MUTED, bg=CHIP).pack(anchor="w")
        value = tk.Label(frame, text="0", font=font, fg="white", bg=CHIP, width=width, anchor="w")
        value.pack(anchor="w")
        return value

    def _build_ui(self) -> None:
        root = self.root
        root.configure(bg=BG)
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<KeyPress>", self._on_key)
        self._value_font = tkfont.Font(root=root, family=self._ui_family, size=17, weight="bold")
        self._mono_font = tkfont.Font(root=root, family=self._mono_family, size=17, weight="bold")
        self._cell_font = tkfont.Font(root=root, family=self._ui_family, size=12, weight="bold")
        self._banner_font = tkfont.Font(root=root, family=self._ui_family, size=22, weight="bold")
        self._banner_sub_font = tkfont.Font(root=root, family=self._ui_family, size=10, weight="bold")
        self._card_title_font = tkfont.Font(root=root, family=self._ui_family, size=20, weight="bold")
        self._card_body_font = tkfont.Font(root=root, family=self._ui_family, size=10)
        self._card_hint_font = tkfont.Font(root=root, family=self._ui_family, size=8)

        # 顶部：状态芯片 + 操作按钮
        header = tk.Frame(root, bg=BAR)
        header.grid(row=0, column=0, sticky="ew")
        chips = tk.Frame(header, bg=BAR)
        chips.pack(side="left", padx=self._px(8), pady=self._px(6))
        self.mines_value = self._make_chip(chips, "MINES", self._value_font, 4)
        self.time_value = self._make_chip(chips, "TIME", self._mono_font, 0)
        actions = tk.Frame(header, bg=BAR)
        actions.pack(side="right", padx=self._px(6), pady=self._px(5))
        for attr, text, command, bg, active in (
            ("mode_btn", "Dig", self.toggle_mode, ACCENT, ACCENT_HOVER),
            ("hint_btn", "Hint +5s", self.request_hint, NEUTRAL, NEUTRAL_HOVER),
            ("new_btn", "New", self.new_game, NEUTRAL, NEUTRAL_HOVER),
            ("rank_btn", "Rank", self.open_rank, NEUTRAL, NEUTRAL_HOVER),
            ("new_window_btn", "New Window", self.spawn_window, NEUTRAL, NEUTRAL_HOVER),
        ):
            button = self._make_button(actions, text, command, bg=bg, active=active)
            button.pack(side="left", padx=self._px(3))
            setattr(self, attr, button)
        # 爆炸后才出现的复盘按钮；默认不占位
        self.why_btn = self._make_button(actions, "Why", self.show_analysis)

        # 中部：棋盘画布
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0, cursor="hand2")
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)
        if sys.platform == "darwin":
            self.canvas.bind("<Button-2>", self._on_right_click)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 底部：难度 + 最佳成绩 + 昵称
        footer = tk.Frame(root, bg=BAR)
        footer.grid(row=2, column=0, sticky="ew")
        self.diff_buttons: dict[str, tk.Button] = {}
        for difficulty in DIFFICULTIES:
            button = self._make_button(footer, difficulty.label, lambda d=difficulty: self.set_difficulty(d))
            button.pack(side="left", padx=self._px(3), pady=self._px(6))
            self.diff_buttons[difficulty.key] = button
        right = tk.Frame(footer, bg=BAR)
        right.pack(side="right", padx=self._px(6), pady=self._px(6))
        self.name_entry = tk.Entry(right, textvariable=self._name_var, width=12,
                                   font=(self._ui_family, 12), bg=NEUTRAL, fg="white",
                                   insertbackground="white", relief="flat", bd=0, highlightthickness=0)
        self.name_entry.pack(side="right", padx=(self._px(4), self._px(8)), ipady=self._px(5))
        self.name_entry.bind("<KeyRelease>", self._on_name_changed)
        self.name_entry.bind("<FocusOut>", lambda _event: self._persist_name())
        tk.Label(right, text="NAME", font=(self._ui_family, 7, "bold"), fg=MUTED, bg=BAR).pack(
            side="right", padx=(self._px(8), 0))
        tk.Frame(right, bg=BAR, width=self._px(16), height=1).pack(side="right")
        self.best_value = tk.Label(right, text="-", font=(self._ui_family, 11, "bold"), fg="#cfd8dc", bg=BAR)
        self.best_value.pack(side="right", padx=(self._px(4), 0))
        tk.Label(right, text="BEST", font=(self._ui_family, 7, "bold"), fg=MUTED, bg=BAR).pack(
            side="right", padx=(self._px(8), 0))
        # 让键盘事件有明确的焦点控件，否则快捷键不会触发（Entry 聚焦时仍按规则忽略）
        self.root.after(100, self._focus_board)

    def _apply_window(self, geometry: tuple[int, int] | None) -> None:
        """窗口几何：默认 1020x780 逻辑像素、居中，且不超过小屏幕可容纳的范围。"""
        root = self.root
        screen_w, screen_h = max(1, root.winfo_screenwidth()), max(1, root.winfo_screenheight())
        # 最小尺寸 / 指定尺寸都限制在屏幕 90% 内，避免小屏或高 DPI 下窗口超出屏幕
        max_w, max_h = max(1, int(screen_w * 0.9)), max(1, int(screen_h * 0.9))
        root.minsize(min(self._px(720), max_w), min(self._px(560), max_h))
        if geometry is not None:
            width, height = geometry
        else:
            width = min(self._px(1020), int(screen_w * 0.92))
            height = min(self._px(780), int(screen_h * 0.92))
        width = min(max(1, int(width)), max_w)
        height = min(max(1, int(height)), max_h)
        root.geometry(f"{width}x{height}+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 2)}")

    def _focus_board(self) -> None:
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass

    # ---- 难度 / 模式 / 新局 ------------------------------------------
    def new_game(self, difficulty: Difficulty | None = None) -> None:
        if difficulty is not None:
            self.difficulty = difficulty
        self.epoch += 1  # 旧线程的结果一律作废
        if self.busy:
            self._set_busy(False)
        self._cancel_after("_after_poll")
        self.game = AnalysedMinesweeper(Size(self.difficulty.cols, self.difficulty.rows),
                                        self.difficulty.mines)
        self.over = self.won = self.lost = False
        self.final_elapsed = self.lost_cell = self.banner = None
        self.analysis, self._card_visible = None, False
        self.hint_labels, self.hint_active = None, False
        self.time_penalty = 0.0
        self._revealed_before = 0
        self.hint_btn.config(bg=NEUTRAL, activebackground=NEUTRAL_HOVER)
        self._set_why_visible(False)
        self.mines_value.config(fg="white")
        self._chip_error = self.flag_mode = self._press_active = self._pending_redraw = False
        self._press_cell = None
        self._persist_name()
        self._update_title()
        self._update_difficulty_buttons()
        self._update_mode_btn()
        self._update_chips()
        self._update_best_label()
        self._redraw()

    def set_difficulty(self, difficulty: Difficulty) -> None:
        if difficulty.key != self.difficulty.key:
            self.new_game(difficulty)

    def toggle_mode(self) -> None:
        self.flag_mode = not self.flag_mode
        self._update_mode_btn()

    def _update_mode_btn(self) -> None:
        if self.flag_mode:
            self.mode_btn.config(text="Flag", bg=DANGER, activebackground=DANGER_HOVER)
        else:
            self.mode_btn.config(text="Dig", bg=ACCENT, activebackground=ACCENT_HOVER)

    def _update_title(self) -> None:
        self.root.title(f"Guessless - {self.difficulty.label}")

    def _update_difficulty_buttons(self) -> None:
        for key, button in self.diff_buttons.items():
            active = key == self.difficulty.key
            button.config(bg=ACCENT if active else NEUTRAL,
                          activebackground=ACCENT_HOVER if active else NEUTRAL_HOVER)

    # ---- 计时与状态 --------------------------------------------------
    def _current_seconds(self) -> float:
        if self.final_elapsed is not None:
            return self.final_elapsed
        game = self.game
        if game is not None and game.state is GameState.RUNNING and game.t_start is not None:
            return max(0.0, time.time() - game.t_start) + self.time_penalty
        return 0.0

    def _tick_timer(self) -> None:
        self._after_timer = None
        if self._closed:
            return
        self._update_timer_label()
        self._reap_children()
        self._after_timer = self.root.after(150, self._tick_timer)

    def _update_timer_label(self) -> None:
        self.time_value.config(text=format_time(self._current_seconds()))

    def _flags_used(self) -> int:
        game = self.game
        return sum(1 for cell in game.board.values() if cell.flagged) if game is not None else 0

    def _update_chips(self) -> None:
        game = self.game
        if game is None:
            return
        if self._chip_error:
            self.mines_value.config(text="ERR", fg=LOSE_COLOR, width=4)
        elif self.lost:
            self.mines_value.config(text="BOOM", fg=LOSE_COLOR, width=0)
        elif self.busy:
            self.mines_value.config(text=THINKING[self._thinking_index], fg="white", width=4)
        else:
            self.mines_value.config(text=str(game.n_mines - self._flags_used()), fg="white", width=4)
        self._update_timer_label()

    # ---- 异步引擎调用 ------------------------------------------------
    def _set_busy(self, value: bool) -> None:
        self.busy = value
        if value:
            self._thinking_index = 0
            self.mines_value.config(text=THINKING[0])
            self._after_thinking = self.root.after(220, self._tick_thinking)
            if self._after_poll is None:
                self._after_poll = self.root.after(40, self._poll_worker)
        else:
            self._cancel_after("_after_thinking")
            self._update_chips()

    def _tick_thinking(self) -> None:
        self._after_thinking = None
        if self._closed or not self.busy:
            return
        self._thinking_index = (self._thinking_index + 1) % len(THINKING)
        self.mines_value.config(text=THINKING[self._thinking_index])
        self._after_thinking = self.root.after(220, self._tick_thinking)

    def _start_worker(self, cell: Position) -> None:
        game = self.game
        if game is None:
            return
        epoch, results = self.epoch, self._results
        self._active_click = cell
        self._revealed_before = self._revealed_count()
        self._set_busy(True)

        def solve() -> None:
            # 工作线程只改游戏对象并把普通元组塞进队列，绝不碰 Tk
            error: str | None = None
            elapsed: float | None = None
            try:
                game.reveal_at(cell)
            except Exception:
                traceback.print_exc()
                error = "engine error"
            try:
                if game.t_start is not None and game.state in (GameState.WON, GameState.LOST):
                    elapsed = max(0.0, time.time() - game.t_start)
            except Exception:
                elapsed = None
            results.put((epoch, "dig", error, elapsed, None))

        threading.Thread(target=solve, name="guessless-reveal", daemon=True).start()

    def request_hint(self) -> None:
        """花 5 秒罚时显示一次全盘三色提示，直到下一格被揭开。"""
        game = self.game
        if (game is None or self.busy or self.over or self.hint_active
                or game.state is not GameState.RUNNING):
            return
        epoch, results = self.epoch, self._results
        self._set_busy(True)

        def solve() -> None:
            # 提示只读求解，不改棋盘；结果交回主线程再应用罚时
            error: str | None = None
            result = None
            try:
                result = game.solve()
            except Exception:
                traceback.print_exc()
                error = "engine error"
            results.put((epoch, "hint", error, None, result))

        threading.Thread(target=solve, name="guessless-hint", daemon=True).start()

    def _revealed_count(self) -> int:
        game = self.game
        return sum(1 for cell in game.board.values() if cell.revealed) if game is not None else 0

    def _poll_worker(self) -> None:
        self._after_poll = None
        if self._closed:
            return
        finished: WorkerResult | None = None
        while True:
            try:
                item = self._results.get_nowait()
            except queue.Empty:
                break
            if item[0] == self.epoch:  # 旧 epoch 的结果直接丢弃
                finished = item
        if finished is not None:
            _, kind, error, elapsed, payload = finished
            if kind == "hint":
                self._finish_hint(error, payload)
            else:
                self._finish_worker(error, elapsed)
        elif self.busy:
            self._after_poll = self.root.after(40, self._poll_worker)

    def _finish_hint(self, error: str | None, payload: object) -> None:
        self._set_busy(False)
        game = self.game
        if (error is not None or payload is None or game is None
                or game.state is not GameState.RUNNING):
            self._redraw()
            return
        try:
            labels = classify_hidden(game, payload)  # type: ignore[arg-type]
        except Exception:
            traceback.print_exc()
            self._redraw()
            return
        if not labels:  # 没有可提示的暗格，不罚时
            self._redraw()
            return
        self.hint_labels, self.hint_active = labels, True
        self.time_penalty += HINT_PENALTY
        self.hint_btn.config(bg=HINT_COLOR, activebackground=HINT_HOVER)
        self._update_timer_label()
        self._redraw()

    def _clear_hint(self) -> None:
        self.hint_active = False
        self.hint_labels = None
        self.hint_btn.config(bg=NEUTRAL, activebackground=NEUTRAL_HOVER)

    def _finish_worker(self, error: str | None, elapsed: float | None) -> None:
        self._set_busy(False)
        game = self.game
        if error is not None or game is None:
            self._chip_error, self.over = True, True
            self._clear_hint()
            self._update_chips()
            self._redraw()
            return
        if game.state is GameState.WON:
            self.over = self.won = True
            final = elapsed if elapsed is not None else self._current_seconds()
            if elapsed is not None:
                final += self.time_penalty
            self.final_elapsed = max(0.0, final)
            for cell in game.board.values():
                if not cell.revealed:  # 胜利时剩余暗格必定是雷，直接插旗展示
                    cell.flagged = True
            new_best = self._record_win(self.final_elapsed)
            self.banner = ("win", f"WIN {format_time(self.final_elapsed)}", "NEW BEST" if new_best else None)
            self.analysis, self._card_visible = None, False
            self._clear_hint()
            self._set_why_visible(False)
            self._refresh_rank_if_open()
        elif game.state is GameState.LOST:
            self.over = self.lost = True
            final = elapsed if elapsed is not None else self._current_seconds()
            if elapsed is not None:
                final += self.time_penalty
            self.final_elapsed = max(0.0, final)
            self.analysis = self._build_loss_analysis(game)
            self._card_visible = self.analysis is not None
            self.lost_cell = (self.analysis.position if self.analysis is not None
                              else self._active_click)
            self.banner = None if self.analysis is not None else ("lost", "BOOM", None)
            self._clear_hint()
            self._set_why_visible(self.analysis is not None)
        elif self.hint_active and self._revealed_count() > self._revealed_before:
            self._clear_hint()  # 揭开下一格后提示自动失效
        self._update_chips()
        self._update_best_label()
        self._redraw()

    def _build_loss_analysis(self, game: Minesweeper) -> LossAnalysis | None:
        """取引擎记录的决策快照；复盘失败时退回普通 BOOM 提示。"""
        try:
            return analyse_loss(game, getattr(game, "last_decision", None))
        except Exception:
            traceback.print_exc()
            return None

    # ---- 棋盘输入 ----------------------------------------------------
    def _on_press(self, event: tk.Event) -> None:
        self._focus_board()  # 点击棋盘后快捷键重新生效（Entry 会因此落焦存储昵称）
        if self._card_visible:
            self._card_visible = False  # 先收起复盘卡片，方便查看棋盘标记
            self._redraw()
            return
        if self.busy or self.over:
            return
        self._press_cell = self._cell_at(event.x, event.y)
        self._press_time = time.monotonic()
        self._press_active = self._press_cell is not None
        if self._press_active:
            self._redraw()

    def _on_release(self, event: tk.Event) -> None:
        cell = self._cell_at(event.x, event.y)
        pressed, duration = self._press_cell, time.monotonic() - self._press_time
        self._press_cell, self._press_active = None, False
        if not self.busy and not self.over and pressed is not None and cell == pressed:
            if duration >= LONG_PRESS or self.flag_mode:
                self._flag(cell)
            else:
                self._dig(cell)
        self._redraw()

    def _on_right_click(self, event: tk.Event) -> None:
        if self._card_visible:
            self._card_visible = False
            self._redraw()
            return
        if self.busy or self.over:
            return
        cell = self._cell_at(event.x, event.y)
        if cell is not None:
            self._flag(cell)

    def _dig(self, cell: Position) -> None:
        game = self.game
        if self.busy or self.over or game is None:
            return
        state = game.get_at(cell)
        if state.flagged:
            return
        if state.revealed:  # 和弦：没有可翻邻居时无需打扰求解线程
            neighbours = game.enum_adj(cell)
            if not state.number or sum(game.get_at(p).flagged for p in neighbours) != state.number:
                return
            if not any(not game.get_at(p).revealed for p in neighbours):
                return
        self._start_worker(cell)

    def _flag(self, cell: Position) -> None:
        game = self.game
        if self.busy or self.over or game is None or game.get_at(cell).revealed:
            return
        game.flag_at(cell)  # 首挖前引擎会静默忽略，这里不报错
        self._update_chips()
        self._redraw()

    def _cell_at(self, px: int, py: int) -> Position | None:
        game = self.game
        if game is None or self._cell <= 0:
            return None
        x = int((px - self._ox) // self._cell)
        y = int((py - self._oy) // self._cell)
        return Position(x, y) if 0 <= x < game.size.w and 0 <= y < game.size.h else None

    # ---- 绘制 --------------------------------------------------------
    def _on_canvas_configure(self, _event: tk.Event) -> None:
        self._cancel_after("_after_redraw")
        self._after_redraw = self.root.after(20, self._redraw)

    def _redraw(self) -> None:
        self._after_redraw = None
        if self._closed:
            return
        if self.busy:  # 求解期间不读棋盘，记下待重绘
            self._pending_redraw = True
            return
        self._pending_redraw = False
        game = self.game
        self.canvas.delete("all")
        if game is None:
            return
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        self._cell, self._ox, self._oy = board_metrics(
            width, height, game.size.w, game.size.h, max(4, self._px(10)),
            max(6, self._px(12)), max(24, self._px(120)))
        self._cell_font.configure(size=max(8, int(self._cell * 0.45)))
        for y in range(game.size.h):
            for x in range(game.size.w):
                self._draw_cell(Position(x, y))
        if self.analysis is not None:
            self._draw_analysis_highlights()
            if self._card_visible:
                self._draw_analysis_card(width, height)
        if self.banner is not None:
            self._draw_banner(width, height)

    def _draw_cell(self, position: Position) -> None:
        game = self.game
        if game is None:
            return
        state, cell = game.get_at(position), self._cell
        x0, y0 = self._ox + position.x * cell, self._oy + position.y * cell
        x1, y1 = x0 + cell, y0 + cell
        edge = max(1, self._px(2))
        pressed = self._press_active and self._press_cell == position
        label = ""
        fill, outline = HIDDEN, HIDDEN_EDGE
        if not state.revealed:
            labels = (self.analysis.labels if self.analysis is not None
                      else self.hint_labels if self.hint_active else None)
            if labels is not None:
                label = labels.get(position, "")
                if label in MARKER_FILLS:  # 爆炸复盘/提示：直接填色，比描边醒目
                    fill, outline = MARKER_FILLS[label], MARKER_EDGES[label]
        if state.revealed:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=REVEALED, outline=GRID)
            if state.number:
                self.canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=str(state.number),
                                        font=self._cell_font, fill=NUM_COLORS.get(state.number, "#000000"))
        elif self.lost and self.lost_cell == position:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=EXPLODED_FILL,
                                         outline=EXPLODED_EDGE, width=edge)
            self._draw_mine(x0, y0, cell, color="#ffffff")
        elif state.flagged:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=edge)
            self._draw_flag(x0, y0, cell, color=MINE if label else FLAG)
        else:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=PRESS if pressed else fill,
                                         outline=outline, width=edge)
        if pressed and state.revealed:
            self.canvas.create_rectangle(x0 + 1, y0 + 1, x1 - 1, y1 - 1, outline=ACCENT, width=edge)

    def _draw_analysis_highlights(self) -> None:
        """给推出爆炸结论的数字格描边，方便对照卡片上的坐标。"""
        analysis, cell = self.analysis, self._cell
        if analysis is None or cell <= 0:
            return
        inset = max(2, self._px(3))
        for position in analysis.highlight:
            x0 = self._ox + position.x * cell
            y0 = self._oy + position.y * cell
            self.canvas.create_rectangle(x0 + inset, y0 + inset,
                                         x0 + cell - inset, y0 + cell - inset,
                                         outline=ACCENT, width=max(2, self._px(3)))

    def _draw_analysis_card(self, width: int, height: int) -> None:
        """画布中央的爆炸复盘卡片：原因、三分类计数、触发的限制。"""
        analysis = self.analysis
        if analysis is None:
            return
        margin = self._px(10)
        card_w = max(self._px(280), min(width - 2 * margin, self._px(600)))
        pad = self._px(16)
        inner = max(self._px(80), card_w - 2 * pad)
        reason, limit = describe_loss(analysis)
        first = f"{reason} · {analysis.solutions} 个一致盘面"
        wrapped_reason = self._wrap_text(first, self._card_body_font, inner)
        wrapped_limit = self._wrap_text(limit, self._card_body_font, inner)
        body_linespace = self._card_body_font.metrics("linespace")
        hint_linespace = self._card_hint_font.metrics("linespace")
        title_linespace = self._card_title_font.metrics("linespace")
        counts_linespace = max(body_linespace, self._px(12))
        gap = self._px(6)
        card_h = (2 * pad + title_linespace + gap
                  + len(wrapped_reason) * body_linespace + gap
                  + counts_linespace + gap
                  + len(wrapped_limit) * body_linespace + gap
                  + hint_linespace)
        card_h = min(card_h, max(self._px(140), height - 2 * margin))
        x0 = (width - card_w) // 2
        y0 = (height - card_h) // 2
        _draw_panel(self.canvas, x0, y0, x0 + card_w, y0 + card_h,
                    self._px(10), BAR, LOSE_COLOR, max(2, self._px(3)))
        left = x0 + pad
        top = y0 + pad
        self.canvas.create_text(left, top, text="BOOM", anchor="nw",
                                font=self._card_title_font, fill=LOSE_COLOR)
        top += title_linespace + gap
        for line in wrapped_reason:
            self.canvas.create_text(left, top, text=line, anchor="nw",
                                    font=self._card_body_font, fill="#eceff1")
            top += body_linespace
        top += gap
        self._draw_analysis_counts(left, top, counts_linespace)
        top += counts_linespace + gap
        for line in wrapped_limit:
            self.canvas.create_text(left, top, text=line, anchor="nw",
                                    font=self._card_body_font, fill="#cfd8dc")
            top += body_linespace
        top += gap
        self.canvas.create_text(left, top, text="点击棋盘关闭 · Why 重看", anchor="nw",
                                font=self._card_hint_font, fill=MUTED)

    def _draw_analysis_counts(self, x: float, y: float, line_height: int) -> None:
        analysis = self.analysis
        if analysis is None:
            return
        square = self._px(10)
        cursor = x
        for color, label, count in ((MARKER_FILLS["safe"], "安全", analysis.safe_count),
                                    (MARKER_FILLS["mine"], "必雷", analysis.mine_count),
                                    (MARKER_FILLS["uncertain"], "不确定", analysis.uncertain_count)):
            text = f"{label} {count}"
            self.canvas.create_rectangle(cursor, y + (line_height - square) / 2,
                                         cursor + square, y + (line_height + square) / 2,
                                         fill=color, outline=color)
            self.canvas.create_text(cursor + square + self._px(5), y + line_height / 2,
                                    text=text, anchor="w",
                                    font=self._card_body_font, fill="#eceff1")
            cursor += square + self._px(5) + self._card_body_font.measure(text) + self._px(18)

    @staticmethod
    def _wrap_text(text: str, font: tkfont.Font, max_width: int) -> list[str]:
        """按像素宽度逐字换行（中英文混排都适用）。"""
        lines: list[str] = []
        current = ""
        for char in text:
            if char == "\n":
                lines.append(current)
                current = ""
            elif current and font.measure(current + char) > max_width:
                lines.append(current)
                current = char
            else:
                current += char
        lines.append(current)
        return lines

    def _draw_mine(self, x0: float, y0: float, cell: int, color: str = MINE) -> None:
        cx, cy, radius, line = x0 + cell / 2, y0 + cell / 2, cell * 0.2, max(2, int(cell * 0.06))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            self.canvas.create_line(cx, cy, cx + dx * radius * 1.9, cy + dy * radius * 1.9,
                                    fill=color, width=line)
        self.canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                                fill=color, outline=color)

    def _draw_flag(self, x0: float, y0: float, cell: int, color: str = FLAG) -> None:
        cx, line = x0 + cell * 0.32, max(2, int(cell * 0.08))
        self.canvas.create_line(cx, y0 + cell * 0.18, cx, y0 + cell * 0.82, fill=MINE, width=line)
        self.canvas.create_line(x0 + cell * 0.2, y0 + cell * 0.82, x0 + cell * 0.62, y0 + cell * 0.82,
                                fill=MINE, width=line)
        self.canvas.create_polygon(cx, y0 + cell * 0.2, x0 + cell * 0.7, y0 + cell * 0.36,
                                   cx, y0 + cell * 0.52, fill=color, outline=color)

    def _draw_banner(self, width: int, height: int) -> None:
        if self.banner is None:
            return
        kind, text, subtext = self.banner
        accent = WIN_COLOR if kind == "win" else LOSE_COLOR
        compact = width < self._px(380)
        big, sub = self._banner_font, self._banner_sub_font
        big.configure(size=15 if compact else 22)
        sub.configure(size=8 if compact else 10)
        line_big, line_sub = big.metrics("linespace"), sub.metrics("linespace")
        gap = self._px(4) if subtext else 0
        inner_x, inner_y = (self._px(10), self._px(6)) if compact else (self._px(20), self._px(12))
        panel_w = max(big.measure(text), sub.measure(subtext) if subtext else 0) + 2 * inner_x
        panel_h = line_big + (gap + line_sub if subtext else 0) + 2 * inner_y
        panel_w = min(max(panel_w, self._px(90)), max(8, width - 6))
        panel_h = min(max(panel_h, self._px(46)), max(8, height - 6))
        x0, y0 = (width - panel_w) // 2, (height - panel_h) // 2
        _draw_panel(self.canvas, x0, y0, x0 + panel_w, y0 + panel_h,
                    self._px(10), BAR, accent, max(2, self._px(3)))
        content_h = line_big + (gap + line_sub if subtext else 0)
        top = y0 + (panel_h - content_h) / 2
        self.canvas.create_text(x0 + panel_w / 2, top + line_big / 2, text=text, font=big, fill=accent)
        if subtext:
            self.canvas.create_text(x0 + panel_w / 2, top + line_big + gap + line_sub / 2,
                                    text=subtext, font=sub, fill=WIN_SUB)

    # ---- 排行榜 / 昵称 ----------------------------------------------
    def _remembered_name(self) -> str:
        if self.lb is None:
            return DEFAULT_NAME
        try:
            return sanitize_name(self.lb.last_name())
        except Exception:
            traceback.print_exc()
            return DEFAULT_NAME

    def player_name(self) -> str:
        try:
            return sanitize_name(self._name_var.get())
        except tk.TclError:
            return DEFAULT_NAME

    def _lb_best(self, key: str) -> Entry | None:
        if self.lb is None:
            return None
        try:
            self.lb.refresh()
            return self.lb.best(key)
        except Exception:
            traceback.print_exc()
            return None

    def lb_top(self, key: str) -> list[Entry]:
        if self.lb is None:
            return []
        try:
            self.lb.refresh()
            return list(self.lb.top(key, 10))
        except Exception:
            traceback.print_exc()
            return []

    def lb_warning(self) -> str | None:
        if self.lb is None:
            return "leaderboard unavailable"
        try:
            return self.lb.warning
        except Exception:
            return None

    def _update_best_label(self) -> None:
        best = self._lb_best(self.difficulty.key)
        self.best_value.config(text=format_time(best.seconds) if best is not None else "-")

    def _record_win(self, seconds: float) -> bool:
        """胜利时保存唯一一条记录；返回是否刷新了本难度最佳成绩。"""
        name = sanitize_name(self._name_var.get())
        self._name_var.set(name)
        self._persist_name()
        if self.lb is None:
            return False
        key = self.difficulty.key
        before = self._lb_best(key)
        try:
            self.lb.add(name, key, max(float(seconds), 0.001))
        except Exception:
            traceback.print_exc()
            return False
        # 只比较前后最佳成绩：不依赖 add() 返回的具体 Entry（多窗口/实现细节都可能变化）
        after = self._lb_best(key)
        return after is not None and (before is None or after.seconds < before.seconds)

    def _on_name_changed(self, _event: tk.Event | None = None) -> None:
        self._cancel_after("_after_name")
        self._after_name = self.root.after(500, self._persist_name)

    def _persist_name(self) -> None:
        self._after_name = None
        if self.lb is None:
            return
        try:
            self.lb.set_last_name(self._name_var.get())
        except Exception:
            traceback.print_exc()
            return
        self._refresh_rank_if_open()

    def _refresh_rank_if_open(self) -> None:
        dialog = self._rank_dialog
        if dialog is not None and dialog.winfo_exists():
            try:
                dialog.refresh()
            except tk.TclError:
                pass

    def open_rank(self) -> None:
        self._update_best_label()  # 其它窗口可能刚写入新纪录，先刷新 BEST 显示
        dialog = self._rank_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.deiconify()
            dialog.lift()
            dialog.focus_force()
            dialog.refresh()
            return
        try:
            self._rank_dialog = RankDialog(self)
        except tk.TclError:
            traceback.print_exc()
            self._rank_dialog = None

    def _close_rank_dialog(self) -> None:
        dialog = self._rank_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.close()

    def rank_closed(self, dialog: "RankDialog") -> None:
        if self._rank_dialog is dialog:
            self._rank_dialog = None

    # ---- 爆炸复盘 ----------------------------------------------------
    def show_analysis(self) -> None:
        """重新打开复盘卡片（爆炸后 Why 按钮）。"""
        if self.analysis is None:
            return
        self._card_visible = True
        self._redraw()

    def _set_why_visible(self, visible: bool) -> None:
        if visible:
            if not self.why_btn.winfo_manager():
                self.why_btn.pack(side="left", padx=self._px(3), before=self.new_window_btn)
        elif self.why_btn.winfo_manager():
            self.why_btn.pack_forget()

    # ---- 多窗口 / 关闭 -----------------------------------------------
    def spawn_window(self) -> None:
        """用独立子进程打开同难度的第二个窗口。"""
        try:
            kwargs: dict[str, object] = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            child = subprocess.Popen(build_child_command(self.difficulty.key), **kwargs)
            self._children.append(child)
        except OSError:
            traceback.print_exc()
            try:
                messagebox.showerror("Guessless", "Could not open a new window.", parent=self.root)
            except tk.TclError:
                pass

    def _reap_children(self) -> None:
        """回收已退出的子窗口进程，避免长期多开时残留僵尸进程。"""
        if self._children:
            self._children = [child for child in self._children if child.poll() is None]

    def _on_key(self, event: tk.Event) -> str | None:
        keysym = event.keysym
        if keysym == "Escape":
            if self._rank_dialog is not None and self._rank_dialog.winfo_exists():
                self._close_rank_dialog()
            elif self._card_visible:
                self._card_visible = False
                self._redraw()
            return "break"
        if self._entry_focused():  # 昵称输入时不抢字母快捷键
            return None
        lowered = keysym.lower()
        if lowered == "n":
            self.new_game()
        elif lowered == "m":
            self.toggle_mode()
        elif lowered == "r":
            self.open_rank()
        elif lowered in ("1", "2", "3"):
            self.set_difficulty(DIFFICULTIES[int(lowered) - 1])
        return None

    def _entry_focused(self) -> bool:
        try:
            return isinstance(self.root.focus_get(), tk.Entry)
        except (KeyError, tk.TclError):
            return False

    def _cancel_after(self, attribute: str) -> None:
        after_id = getattr(self, attribute, None)
        if after_id is None:
            return
        setattr(self, attribute, None)
        try:
            self.root.after_cancel(after_id)
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        self._persist_name()
        dialog = self._rank_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
            self._rank_dialog = None
        self._closed = True
        for attribute in ("_after_timer", "_after_redraw", "_after_poll", "_after_thinking", "_after_name"):
            self._cancel_after(attribute)
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def _draw_panel(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
                radius: int, fill: str, outline: str, width: int) -> None:
    """画一个圆角面板（用矩形 + 四分之一圆近似）。"""
    r = max(0, min(int(radius), int((x1 - x0) / 2), int((y1 - y0) / 2)))
    if r <= 0:
        canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=width)
        return
    canvas.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline="")
    canvas.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, outline="")
    # (左上角 x, 左上角 y, 起始角度)：Tk 角度以正东为 0、逆时针增长
    corners = ((x0, y0, 90), (x1 - 2 * r, y0, 0), (x1 - 2 * r, y1 - 2 * r, 270), (x0, y1 - 2 * r, 180))
    for cx, cy, start in corners:
        canvas.create_arc(cx, cy, cx + 2 * r, cy + 2 * r, start=start, extent=90,
                          style="pieslice", fill=fill, outline="")
    canvas.create_line(x0 + r, y0, x1 - r, y0, fill=outline, width=width)
    canvas.create_line(x0 + r, y1, x1 - r, y1, fill=outline, width=width)
    canvas.create_line(x0, y0 + r, x0, y1 - r, fill=outline, width=width)
    canvas.create_line(x1, y0 + r, x1, y1 - r, fill=outline, width=width)
    for cx, cy, start in corners:
        canvas.create_arc(cx, cy, cx + 2 * r, cy + 2 * r, start=start, extent=90,
                          style="arc", outline=outline, width=width)


class RankDialog(tk.Toplevel):
    """排行榜弹窗：难度标签 + 前 10 名；非模态，可缩放，Esc 关闭。"""

    def __init__(self, app: GuesslessApp) -> None:
        super().__init__(app.root)
        self.app = app
        self._active_key = app.difficulty.key
        self.title("Guessless - Ranks")
        self.configure(bg=BG)
        self.transient(app.root)
        try:
            self.wm_attributes("-type", "dialog")  # 平铺 WM 上保持浮动；Windows 不支持则忽略
        except tk.TclError:
            pass
        self.minsize(app._px(360), app._px(320))
        self.geometry(self._initial_geometry())
        tabs = tk.Frame(self, bg=BAR)
        tabs.pack(side="top", fill="x")
        self._tab_buttons: dict[str, tk.Button] = {}
        for difficulty in DIFFICULTIES:
            button = tk.Button(tabs, text=difficulty.label, font=(app.ui_family, 10, "bold"),
                               command=lambda d=difficulty: self._select(d.key), bg=NEUTRAL, fg="white",
                               activebackground=NEUTRAL_HOVER, activeforeground="white", relief="flat",
                               bd=0, highlightthickness=0, cursor="hand2",
                               padx=app._px(12), pady=app._px(7))
            button.pack(side="left", padx=app._px(4), pady=app._px(6))
            self._tab_buttons[difficulty.key] = button
        body = tk.Frame(self, bg=BG)
        body.pack(side="top", fill="both", expand=True)
        self._body_canvas = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
        self._body_canvas.pack(side="left", fill="both", expand=True,
                               padx=(app._px(10), 0), pady=app._px(8))
        self._scrollbar = tk.Scrollbar(body, orient="vertical", command=self._body_canvas.yview,
                                       bg=NEUTRAL, troughcolor=BG, activebackground=NEUTRAL_HOVER,
                                       highlightthickness=0, bd=0, relief="flat", width=app._px(12))
        self._body_canvas.configure(yscrollcommand=self._scrollbar.set)
        self._rows = tk.Frame(self._body_canvas, bg=BG)
        self._rows_id = self._body_canvas.create_window((0, 0), window=self._rows, anchor="nw")
        self._rows.bind("<Configure>", lambda _event: self._sync_scroll())
        self._body_canvas.bind("<Configure>", self._on_body_configure)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)
        self._warning = tk.Label(self, text="", font=(app.ui_family, 8), fg=MUTED, bg=BAR,
                                 anchor="w", justify="left", wraplength=app._px(400))
        self._warning.pack(side="bottom", fill="x")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        self.bind("<Configure>", self._on_configure)
        self.refresh()
        self.focus_set()

    def _initial_geometry(self) -> str:
        root = self.app.root
        root.update_idletasks()
        width, height = self.app._px(460), self.app._px(470)
        x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 2)
        return f"{width}x{height}+{x}+{y}"

    def _on_configure(self, _event: tk.Event) -> None:
        self._warning.config(wraplength=max(self.app._px(160), self.winfo_width() - self.app._px(20)))

    def _on_body_configure(self, event: tk.Event) -> None:
        self._body_canvas.itemconfigure(self._rows_id, width=event.width)
        self._sync_scroll()

    def _sync_scroll(self) -> None:
        """内容超出可见高度时显示滚动条，避免最小尺寸下裁掉最后几行。"""
        self._rows.update_idletasks()
        bbox = self._body_canvas.bbox("all")
        if bbox is None:
            return
        self._body_canvas.configure(scrollregion=(0, 0, bbox[2], bbox[3]))
        if bbox[3] > self._body_canvas.winfo_height():
            if not self._scrollbar.winfo_ismapped():
                self._scrollbar.pack(side="right", fill="y", before=self._body_canvas)
        elif self._scrollbar.winfo_ismapped():
            self._scrollbar.pack_forget()

    def _on_wheel(self, event: tk.Event) -> str:
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:
            step = -1 if getattr(event, "delta", 0) > 0 else 1
        self._body_canvas.yview_scroll(step, "units")
        return "break"

    def close(self) -> None:
        self.app.rank_closed(self)
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _select(self, key: str) -> None:
        if key != self._active_key:
            self._active_key = key
            self.refresh()

    def refresh(self) -> None:
        entries = self.app.lb_top(self._active_key)
        warning = self.app.lb_warning()
        self._warning.config(text=f"! {warning}" if warning else "")
        for key, button in self._tab_buttons.items():
            active = key == self._active_key
            button.config(bg=ACCENT if active else NEUTRAL,
                          activebackground=ACCENT_HOVER if active else NEUTRAL_HOVER)
        for child in self._rows.winfo_children():
            child.destroy()
        if not entries:
            tk.Label(self._rows, text="No records", font=(self.app.ui_family, 12),
                     fg=MUTED, bg=BG).pack(fill="both", expand=True, pady=self.app._px(40))
        else:
            self._rows.grid_columnconfigure(1, weight=1)
            for column, text in enumerate(("RANK", "NAME", "TIME", "DATE")):
                tk.Label(self._rows, text=text, font=(self.app.ui_family, 7, "bold"), fg=MUTED, bg=BG,
                         anchor="w").grid(row=0, column=column, sticky="ew",
                                          padx=self.app._px(4), pady=(0, self.app._px(3)))
            player = self.app.player_name().casefold()
            for index, entry in enumerate(entries):
                name = str(getattr(entry, "name", "?"))
                highlight = name.casefold() == player
                name_fg = "white" if highlight else "#cfd8dc"
                cells = (
                    (str(index + 1), 3, "center", name_fg, False),
                    (name, 0, "w", name_fg, True),
                    (format_time(getattr(entry, "seconds", 0.0)), 6, "e", name_fg, True),
                    (format_date(getattr(entry, "when", None)), 6, "e",
                     MUTED if not highlight else "white", False),
                )
                for column, (text, width, anchor, fg, bold) in enumerate(cells):
                    family = self.app.mono_family if column >= 2 else self.app.ui_family
                    tk.Label(self._rows, text=text, width=width, anchor=anchor,
                             font=(family, 10, "bold" if bold else "normal"), fg=fg,
                             bg=ROW_HIGHLIGHT if highlight else BG).grid(
                        row=index + 1, column=column, sticky="ew" if column == 1 else "e",
                        padx=self.app._px(4), pady=self.app._px(3))
        self._body_canvas.yview_moveto(0)
        self._sync_scroll()


def _parse_geometry(value: str) -> tuple[int, int]:
    """解析 --geometry WxH（物理像素）。"""
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", value)
    if match is None:
        raise argparse.ArgumentTypeError("geometry must look like WxH")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("geometry must be positive")
    return (width, height)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """命令行参数。"""
    parser = argparse.ArgumentParser(prog="guessless.ui", description="Guessless GUI")
    parser.add_argument("--difficulty", choices=[difficulty.key for difficulty in DIFFICULTIES],
                        default="medium", help="初始难度")
    parser.add_argument("--name", default=None, help="初始昵称")
    parser.add_argument("--geometry", type=_parse_geometry, default=None, metavar="WxH",
                        help="窗口物理像素尺寸")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """程序入口：先做 DPI 适配，再创建 Tk 根窗口。"""
    args = parse_args(argv)
    _enable_dpi_awareness()
    root = tk.Tk()
    app = GuesslessApp(root, difficulty=args.difficulty, name=args.name,
                       geometry=args.geometry)
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
