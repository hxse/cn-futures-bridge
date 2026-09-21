"""快捷键与原生控件适配；未知窗口不盲按 Esc 或 Enter。"""

from collections.abc import Callable
import re
import subprocess
import time

from ..config import Settings
from ..errors import BridgeError
from .native import NativeClient, Window, Windows

TABLES = {"orders": ("F5", 3501, 16, "全部", "alt+a"),
          "working": ("F6", 3501, 9, None, None),
          "positions": ("F3", 3401, 14, "持仓", "alt+s"),
          "trades": ("F4", 4101, 14, "明细", "alt+d"),
          "preorders": ("F8", 4201, 12, "全部", "alt+a")}


class Gui:
    def __init__(self, native: NativeClient, settings: Settings):
        self.native = native
        self.settings = settings
        self.bindings: dict[str, Window] = {}
        self.timeout = settings.execution.step_timeout_ms / 1000
        self.interval = settings.execution.poll_interval_ms / 1000
        self.gap = settings.execution.gui_action_gap_ms / 1000
        self.touched = False

    def _xdo(self, *args: str) -> str:
        try:
            result = subprocess.run(["xdotool", *args], env=self.native.env, capture_output=True,
                                    check=True, timeout=self.timeout)
            return result.stdout.decode().strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise BridgeError("GUI_UNRESPONSIVE", "快捷键进程失败或未在期限内完成") from exc

    def activate(self, title: str | None = None) -> None:
        self.touched = True
        pattern = "快期2-CTP-上期技术-" if title is None else "^" + re.escape(title) + "$"
        matches = self._xdo("search", "--all", "--onlyvisible", "--name", pattern).splitlines()
        if len(matches) != 1:
            raise BridgeError("SERVICE_NOT_READY", "目标窗口身份不唯一")
        self._xdo("windowactivate", "--sync", matches[0])

    def key(self, *keys: str) -> None:
        self.touched = True
        self._xdo("key", "--clearmodifiers", "--delay", str(self.settings.execution.gui_action_gap_ms), *keys)
        if self.gap:
            time.sleep(self.gap)

    def wait(self, predicate: Callable[[], bool], message: str, timeout: float | None = None) -> None:
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise BridgeError("QUERY_TIMEOUT", message, 504)
            time.sleep(self.interval)

    def main(self, snapshot: Windows | None = None) -> Window:
        snapshot = snapshot or self.native.windows()
        matches = [w for w in snapshot.windows if w.root == w.hwnd and "快期2-CTP-上期技术-" in w.text]
        if len(matches) != 1:
            raise BridgeError("SERVICE_NOT_READY", "未确认已登录的 SimNow 主窗口")
        return matches[0]

    def dialogs(self, snapshot: Windows | None = None) -> list[Window]:
        snapshot = snapshot or self.native.windows()
        return [w for w in snapshot.windows if w.root == w.hwnd and w.class_name == "#32770"
                and w.visible and w.text]

    def baseline(self) -> Windows:
        snapshot = self.native.windows()
        main = self.main(snapshot)
        if not main.enabled or self.dialogs(snapshot) or snapshot.flags & 4:
            raise BridgeError("GUI_RESET_FAILED", "存在未归属本次操作的窗口或菜单，请暂停后人工核对")
        return snapshot

    def grid(self, table: str) -> Window:
        key, identifier, columns, filter_name, filter_key = TABLES[table]
        snapshot = self.baseline()
        candidates = [w for w in snapshot.windows if w.class_name == "ListCtrl" and w.id == identifier]
        matches: list[Window] = []
        for window in candidates:
            if self.native.ask(f"inspect {window.hwnd}").columns == columns:
                matches.append(window)
        if len(matches) != 1:
            self.activate();self.key(key)
            snapshot = self.baseline()
            matches = [w for w in snapshot.windows if w.class_name == "ListCtrl" and w.id == identifier
                       and w.visible and self.native.ask(f"inspect {w.hwnd}").columns == columns]
        if len(matches) != 1:
            raise BridgeError("TERMINAL_DATA_INVALID", "表格身份或列布局与固定版本不符", 502)
        window = matches[0]
        if filter_name:
            filters = [w for w in snapshot.windows if w.parent == window.parent and w.class_name == "Button"
                       and w.text.startswith(filter_name)]
            if len(filters) != 1 or filters[0].checked != 1:
                self.activate();self.key(key)
                if filter_key:
                    self.key(filter_key)
                snapshot = self.native.windows()
                filters = [w for w in snapshot.windows if w.parent == window.parent and w.class_name == "Button"
                           and w.text.startswith(filter_name)]
                if len(filters) != 1 or filters[0].checked != 1:
                    raise BridgeError("TERMINAL_DATA_INVALID", "无法确认表格完整筛选范围", 502)
        self.bindings[table] = window
        return window

    def select(self, table: str, window: Window, index: int) -> None:
        self.activate();self.key(TABLES[table][0])
        self.native.select(window.hwnd, index)
        if self.native.windows().focus != window.hwnd:
            raise BridgeError("GUI_RESET_FAILED", "表格选择后的焦点不符")

    def close_dialog(self, window: Window) -> None:
        self.activate(window.text);self.key("Escape")
        self.wait(lambda: all(w.hwnd != window.hwnd for w in self.dialogs()), "本次窗口未关闭")

    def finish(self) -> None:
        if self.touched:
            self._xdo("keyup", "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R")
        self.baseline()
        self.touched = False

    def balance_text(self) -> str:
        snapshot = self.baseline()
        buttons = [w for w in snapshot.windows if w.id == 3009 and w.class_name == "Button" and w.visible]
        if len(buttons) != 1:
            raise BridgeError("TERMINAL_DATA_INVALID", "未找到唯一资金查询按钮", 502)
        self.activate()
        result = self.native.ask(f"focus {buttons[0].hwnd}")
        if result.data.get("focused") is not True:
            raise BridgeError("GUI_RESET_FAILED", "资金查询按钮未取得焦点")
        self.key("space")
        self.wait(lambda: bool(self.dialogs()), "资金查询窗口未按时出现")
        snapshot = self.native.windows()
        dialogs = self.dialogs(snapshot)
        if len(dialogs) != 1:
            raise BridgeError("GUI_RESET_FAILED", "资金查询出现未知窗口组合")
        dialog = dialogs[0]
        if dialog.text == "查询期货账户失败":
            self.close_dialog(dialog)
            raise BridgeError("TERMINAL_DATA_INVALID", "快期资金查询失败，未返回旧余额", 502)
        if dialog.text != "期货资金账户详情":
            raise BridgeError("GUI_RESET_FAILED", "资金查询出现未知窗口，请人工核对")
        fields = [w for w in snapshot.windows if w.root == dialog.hwnd and w.id == 7201]
        if len(fields) != 1:
            self.close_dialog(dialog)
            raise BridgeError("TERMINAL_DATA_INVALID", "资金详情控件不完整", 502)
        text = fields[0].text
        self.close_dialog(dialog)
        self.finish()
        return text

    def login(self) -> None:
        snapshot = self.native.windows()
        login = [w for w in snapshot.windows if w.root == w.hwnd and w.text == "用户登录"]
        if len(login) != 1:
            self.main(snapshot)
            return
        root = login[0].hwnd
        controls = [w for w in snapshot.windows if w.root == root and w.visible]
        # 只选择原包中的上期技术电信2；不自动尝试其他交易环境。
        choices = [(w, i) for w in controls if w.class_name == "ComboBox"
                   for i, text in enumerate(w.items) if "电信2" in text]
        if len(choices) == 1:
            window, index = choices[0];self.native.ask(f"combo {window.hwnd} {index}")
        elif not any("电信2" in w.text for w in controls):
            raise BridgeError("SERVICE_NOT_READY", "登录站点未确认电信2，请通过 VNC 选择原生 SimNow 站点")
        snapshot = self.native.windows()
        controls = [w for w in snapshot.windows if w.root == root and w.visible]
        passwords = [w for w in controls if w.password and w.class_name == "Edit"]
        labels = [w for w in controls if w.class_name == "Static" and re.search("账号|帐号|帐户|账户|用户名", w.text)]
        entries = [w for w in controls if w.class_name in ("Edit", "ComboBox") and not w.password]
        candidates = [(abs(entry.rect[1]-label.rect[1])+abs(entry.rect[0]-label.rect[2]), entry)
                      for label in labels for entry in entries
                      if entry.rect[0] >= label.rect[2]-8 and abs(entry.rect[1]-label.rect[1]) <= 25]
        candidates.sort(key=lambda item: item[0])
        if len(passwords) != 1 or not candidates or (len(candidates)>1 and candidates[0][0]==candidates[1][0]):
            raise BridgeError("SERVICE_NOT_READY", "登录输入控件身份不唯一，请人工核对")
        username = candidates[0][1]
        for window, secret in ((username, self.settings.account.username), (passwords[0], self.settings.account.password)):
            value = secret.get_secret_value().encode("gb18030").hex()
            self.native.ask(f"set_text {window.hwnd} {value}")
        buttons = [w for w in controls if w.class_name == "Button" and w.text.replace("&", "").replace(" ", "").startswith("登录")]
        if len(buttons) != 1:
            raise BridgeError("SERVICE_NOT_READY", "登录按钮身份不唯一")
        self.activate("用户登录");self.native.ask(f"focus {buttons[0].hwnd}");self.key("space")
        self.wait(lambda: any("快期2-CTP-上期技术-电信2" in w.text for w in self.native.windows().windows),
                  "SimNow 登录未完成，未自动重试", self.settings.bridge.startup_timeout_seconds)
        self.baseline()
