"""只登记已识别的终端诊断日志；活动句柄不截断、不删除。"""

from pathlib import Path
import re

TERMINAL_LOG = re.compile(r"\d{4}\.\d{2}\.\d{2}-[\d.]+\.log")
LEGACY_NAMES = {"terminal.log", "wineboot.log", "xvfb.log", "openbox.log", "vnc.log", "novnc.log"}


def log_paths(root: Path, terminal: Path) -> list[Path]:
    candidates = [p for p in root.glob("*.log") if p.name in LEGACY_NAMES]
    directories = {terminal, root.parent / "terminal" / "logs"}
    directories.update((root.parent / "sessions").glob("*/terminal/logs"))
    for directory in directories:
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.parent.resolve()):
            continue
        candidates.extend(p for p in directory.glob("*.log") if TERMINAL_LOG.fullmatch(p.name))
    return list(dict.fromkeys(p for p in candidates if p.is_file() and not p.is_symlink()))


def closed_logs(candidates: list[Path]) -> tuple[list[Path], list[Path]]:
    # 仅淘汰与周期维护需要区分活动句柄；日常容量核算只需路径和大小。
    if not candidates:
        return [], []
    by_path = {p.resolve(): p for p in candidates}
    opened: set[Path] = set()
    for process in Path("/proc").glob("[0-9]*"):
        try:
            for descriptor in (process / "fd").iterdir():
                target = descriptor.resolve()
                if target in by_path:
                    opened.add(by_path[target])
        except (OSError, PermissionError):
            continue
    return [p for p in candidates if p not in opened], list(opened)
