# -*- AI Generated -*-
"""本地排行榜存储（纯标准库）：昵称 + 各难度最快成绩；多窗口共享同一 JSON 文件，磁盘/格式问题只设置 warning，不抛异常。"""

from __future__ import annotations

import errno
import json
import math
import os
import re
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_NAME = "Player"
MAX_NAME_LEN = 16
MAX_ENTRIES_PER_DIFFICULTY = 10
DIFFICULTIES = ("easy", "medium", "hard")

_FILENAME = "leaderboard.json"
_CORRUPT_WARNING = "leaderboard file was corrupt; starting empty"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Entry:
    name: str
    difficulty: str
    seconds: float
    when: float  # 写入时的 epoch 秒


def default_leaderboard_path() -> Path:
    """按平台规则返回排行榜文件路径（环境变量优先）。"""
    custom = os.environ.get("GUESSLESS_DATA_DIR")
    if custom:
        return Path(custom).expanduser() / _FILENAME
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata).expanduser() if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Guessless" / _FILENAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Guessless" / _FILENAME
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share") / "guessless" / _FILENAME


def sanitize_name(name: str | None) -> str:
    """去控制字符、压缩空白、截断到 16 字符；空值用默认名。"""
    if not isinstance(name, str):
        name = "" if name is None else str(name)
    # 先压缩空白（含换行/制表），再删除其余不可打印控制符
    return _CONTROL.sub("", _SPACE.sub(" ", name)).strip()[:MAX_NAME_LEN].strip() or DEFAULT_NAME


def _number(value: object, default: float) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _describe_error(exc: OSError) -> str:
    if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "read-only"
    return exc.strerror or str(exc)


class Leaderboard:
    """排行榜存储；公开方法不会因磁盘问题抛异常（参数校验除外）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.warning: str | None = None
        self._entries: list[Entry] = []
        self._last_name: str = DEFAULT_NAME
        self._storage_warning: str | None = None
        self._corrupt_pending = False
        requested = Path(path) if path is not None else default_leaderboard_path()
        self._path = self._resolve_storage(requested)
        self.refresh()

    def _resolve_storage(self, requested: Path) -> Path:
        reason = self._probe_directory(requested.parent)
        if reason is None:
            return requested
        fallback = Path(tempfile.gettempdir()) / "guessless" / _FILENAME
        self._probe_directory(fallback.parent)  # 回退目录通常可写；失败则后续仅内存保存
        self._storage_warning = f"storage unavailable ({reason}); using fallback {fallback}"
        return fallback

    def _probe_directory(self, directory: Path) -> str | None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, probe = tempfile.mkstemp(prefix=".guessless-probe.", dir=directory)
            os.close(fd)
            os.unlink(probe)
        except OSError as exc:
            return _describe_error(exc)
        return None

    def refresh(self) -> None:
        """重新读取文件（多窗口共享）；读失败时保留内存状态。"""
        entries, last_name, issue = self._load()
        self._entries, self._last_name = entries, last_name
        self._corrupt_pending = self._corrupt_pending or issue == _CORRUPT_WARNING
        self.warning = issue or self._storage_warning

    def _load(self) -> tuple[list[Entry], str, str | None]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return [], DEFAULT_NAME, None
        except OSError as exc:  # 读失败保留内存数据，避免成绩凭空消失
            return list(self._entries), self._last_name, f"storage unavailable ({_describe_error(exc)})"
        except ValueError:
            return [], DEFAULT_NAME, _CORRUPT_WARNING
        if not isinstance(raw, dict):
            return [], DEFAULT_NAME, _CORRUPT_WARNING
        raw_entries = raw.get("entries")
        parsed = (self._parse_entry(item) for item in raw_entries) if isinstance(raw_entries, list) else ()
        # 字典去重（保留首次出现顺序），容错未知字段/错误类型/重复记录
        entries = list({(e.name, e.difficulty, e.seconds, e.when): e for e in parsed if e is not None}.values())
        raw_last = raw.get("last_name")
        return entries, sanitize_name(raw_last if isinstance(raw_last, str) else None), None

    def _parse_entry(self, item: object) -> Entry | None:
        if not isinstance(item, dict):
            return None
        difficulty = item.get("difficulty")
        seconds = _number(item.get("seconds"), -1.0)
        if not isinstance(difficulty, str) or difficulty not in DIFFICULTIES or seconds <= 0:
            return None
        name = item.get("name")
        when = max(_number(item.get("when"), 0.0), 0.0)
        return Entry(sanitize_name(name if isinstance(name, str) else None), difficulty, seconds, when)

    def _save(self) -> bool:
        if self._corrupt_pending:  # 重建前把损坏文件改名备份，绝不直接删除玩家数据
            with suppress(OSError):
                os.replace(self._path, self._path.with_name(self._path.name + ".corrupt"))
            self._corrupt_pending = False
        payload = {"version": 1, "last_name": self._last_name, "entries": [asdict(e) for e in self._entries]}
        temp_path: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=".leaderboard.", suffix=".tmp", dir=self._path.parent)
            temp_path = Path(temp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
            os.replace(temp_path, self._path)
        except OSError as exc:  # 写失败不丢内存成绩，只告警
            self.warning = f"storage unavailable ({_describe_error(exc)}); scores kept in memory"
            return False
        finally:
            if temp_path is not None:
                with suppress(OSError):
                    temp_path.unlink()
        self.warning = self._storage_warning
        return True

    def add(self, name: str | None, difficulty: str, seconds: float) -> Entry:
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"unknown difficulty: {difficulty!r}")
        if (value := _number(seconds, -1.0)) <= 0:
            raise ValueError("seconds must be a positive number")
        self.refresh()
        entry = Entry(sanitize_name(name), difficulty, value, time.time())  # 即使随后被 prune 挤出，也返回本次新增记录
        self._entries.append(entry)
        self._prune()
        self._save()
        return entry

    def _prune(self) -> None:
        kept: list[Entry] = []
        for difficulty in DIFFICULTIES:
            group = sorted((e for e in self._entries if e.difficulty == difficulty), key=lambda e: (e.seconds, e.when))
            kept.extend(group[:MAX_ENTRIES_PER_DIFFICULTY])
        self._entries = kept

    def top(self, difficulty: str, limit: int = 10) -> list[Entry]:
        if limit <= 0:
            return []
        matching = sorted((e for e in self._entries if e.difficulty == difficulty), key=lambda e: (e.seconds, e.when))
        return matching[:limit]

    def best(self, difficulty: str) -> Entry | None:
        return leaders[0] if (leaders := self.top(difficulty, 1)) else None

    def last_name(self) -> str:
        return self._last_name

    def set_last_name(self, name: str) -> None:
        self.refresh()
        self._last_name = sanitize_name(name)
        self._save()
