"""快捷键与原生控件适配；未知窗口不盲按 Esc 或 Enter。"""

from collections.abc import Callable
import logging
import re
import subprocess
import time

from ..config import Settings
from ..errors import BridgeError
from .native import NativeClient, Window, Windows

LOG = logging.getLogger(__name__)
MENU_FLAGS = 4 | 8 | 16
MODIFIER_KEYS = ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R")

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
        self.titles = settings.profile.titles(settings.site)

    def _xdo(self, *args: str) -> str:
        try:
            result = subprocess.run(["xdotool", *args], env=self.native.env, capture_output=True,
                                    check=True, timeout=self.timeout)
            return result.stdout.decode().strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise BridgeError("GUI_UNRESPONSIVE", "快捷键进程失败或未在期限内完成") from exc

    def activate(self, title: str | None = None) -> None:
        self.touched = True
        pattern = "(" + "|".join(re.escape(value) for value in self.titles) + ")$" if title is None else "^" + re.escape(title) + "$"
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
        matches = [w for w in snapshot.windows if w.root == w.hwnd and w.text.endswith(self.titles)]
        if len(matches) != 1:
            raise BridgeError("SERVICE_NOT_READY", "未确认当前环境、券商和站点的唯一主窗口")
        return matches[0]

    def dialogs(self, snapshot: Windows | None = None) -> list[Window]:
        snapshot = snapshot or self.managed_snapshot()
        return [w for w in snapshot.windows if w.root == w.hwnd and w.class_name == "#32770"
                and w.visible]

    def ensure_ready(self, *, keyboard: bool = False) -> None:
        if self.native.poisoned:
            raise BridgeError("GUI_UNRESPONSIVE", "旧原生调用未退出，禁止复位")
        started = time.monotonic()
        deadline = self.native.startup_deadline or started + self.timeout
        posted: set[tuple[str, int]] = set()
        released = False
        while True:
            if time.monotonic() >= deadline:
                raise BridgeError("GUI_RESET_FAILED", "界面恢复未在期限内完成")
            state = self.native.gui_state()
            if state.main_count != 1 or not state.main:
                raise BridgeError("GUI_RESET_FAILED", "未确认唯一配置主窗口，禁止复位")
            if state.unknown_dialogs or state.funds_count > 1:
                raise BridgeError("GUI_RESET_FAILED", "存在未知或不唯一的阻塞窗口，禁止自动确认")
            action: tuple[str, int] | None = None
            if state.document_pending:
                pass
            elif state.flags & MENU_FLAGS:
                if not state.menu_owned or state.dialogs or state.flags & 2:
                    raise BridgeError("GUI_RESET_FAILED", "菜单归属或界面状态不明确，禁止取消")
                action = ("menu", state.main)
            elif state.funds_count == 1 and not state.capture and not state.flags & 2:
                action = ("funds", state.funds)
            elif not state.enabled or state.dialogs or state.capture or state.flags & 2:
                raise BridgeError("GUI_RESET_FAILED", "界面仍被未知窗口或鼠标操作占用")
            elif keyboard and state.modifiers:
                if not released:
                    self._xdo("keyup", *MODIFIER_KEYS)
                    self.touched = released = True
                    LOG.info("已释放残留修饰键", extra={"step": "gui_recovery", "event": "submitted"})
                    continue
            else:
                LOG.info("界面检查完成，恢复动作=%s", len(posted) + int(released),
                         extra={"step": "gui_readiness", "event": "end",
                                "duration_ms": (time.monotonic()-started)*1000})
                return
            if action is not None and action not in posted:
                self.native.ask(f"recover_gui {action[1]}")
                posted.add(action)
                LOG.info("已投递界面恢复：类型=%s，窗口=%s", *action,
                         extra={"step": "gui_recovery", "event": "submitted"})
                continue
            # 恢复后先立即回读；只有仍未结束的动作或通知才轮询等待。
            time.sleep(min(self.interval, max(0, deadline-time.monotonic())))

    def drain_notices(self) -> None:
        self.wait(lambda: not self.native.gui_state().document_pending, "已识别通知未在期限内关闭")

    def managed_snapshot(self) -> Windows:
        # 已知结算单和附属网页仅由持有执行权的流程处理；暂停时不轮询。
        deadline = self.native.startup_deadline or time.monotonic() + self.timeout
        if time.monotonic() >= deadline:
            raise BridgeError("QUERY_TIMEOUT", "文档确认未在期限内完成", 504)
        snapshot = self.native.managed_windows()
        pending = snapshot.document_pending
        while snapshot.document_pending:
            if time.monotonic() >= deadline:
                raise BridgeError("QUERY_TIMEOUT", "文档确认未在期限内完成", 504)
            time.sleep(self.interval)
            snapshot = self.native.managed_windows()
        if pending:
            LOG.info("已识别通知或文档已关闭", extra={"step": "document_confirmation", "event": "end"})
        return snapshot

    def baseline(self) -> Windows:
        snapshot = self.managed_snapshot()
        main = self.main(snapshot)
        if not main.enabled or self.dialogs(snapshot) or snapshot.flags & (MENU_FLAGS | 2) or snapshot.capture:
            self.ensure_ready()
            snapshot = self.managed_snapshot()
            if (not self.main(snapshot).enabled or self.dialogs(snapshot)
                    or snapshot.flags & (MENU_FLAGS | 2) or snapshot.capture):
                raise BridgeError("GUI_RESET_FAILED", "恢复后界面状态再次变化，停止本次操作")
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
        if self.native.poisoned:
            raise BridgeError("GUI_UNRESPONSIVE", "旧原生调用未退出，禁止收尾按键")
        if self.touched:
            self._xdo("keyup", *MODIFIER_KEYS)
        self.ensure_ready(keyboard=self.touched)
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
        snapshot = self.managed_snapshot()
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
        return text

    def prepare_login(self) -> tuple[int, list[Window]]:
        snapshot = self.native.windows()
        login = [w for w in snapshot.windows if w.root == w.hwnd and w.text == "用户登录"]
        if len(login) != 1:
            raise BridgeError("SERVICE_NOT_READY", "需要从新启动的登录窗口按配置登录，不接管未知已有会话")
        root = login[0].hwnd
        controls = [w for w in snapshot.windows if w.root == root and w.visible]
        # 固定版本把券商和站点合为一个选项，填写凭证前须精确选中并回读。
        allowed = tuple(f"{name}-{self.settings.site}" for name in self.settings.profile.title_names)
        choices = [(w, i, text) for w in controls if w.class_name == "ComboBox"
                   for i, text in enumerate(w.items) if text in allowed]
        if len(choices) != 1:
            raise BridgeError("SERVICE_NOT_READY", "配置的券商和站点在登录列表中不唯一")
        window, index, expected = choices[0]
        self.native.ask(f"combo {window.hwnd} {index}")
        current = [w for w in self.native.windows().windows if w.hwnd == window.hwnd]
        if len(current) != 1 or current[0].text != expected:
            raise BridgeError("SERVICE_NOT_READY", "登录券商和站点选择未生效")
        snapshot = self.native.windows()
        controls = [w for w in snapshot.windows if w.root == root and w.visible]
        return root, controls

    def login_ready(self, snapshot: Windows, root: int) -> bool:
        # 只检查已绑定登录窗口的可见非输入控件，不记录账号框或整棵窗口树。
        labels = [w.text.strip().rstrip("。.!！") for w in snapshot.windows
                  if w.root == root and w.visible and w.class_name == "Static"]
        if "连接服务器失败" in labels:
            raise BridgeError("CONNECTION_FAILED", "快期提示连接服务器失败")
        attention = ("密码错误", "密码不正确", "密码不匹配", "用户不存在", "账户被锁", "账号被锁", "验证码")
        if any(part in label for label in labels for part in attention):
            raise BridgeError("LOGIN_REQUIRES_ATTENTION", "登录提示需要核对凭证、账户或验证码，未自动重试")
        if snapshot.document_pending:
            return False
        dialogs = [w for w in self.dialogs(snapshot) if w.hwnd != root]
        if dialogs:
            raise BridgeError("GUI_RESET_FAILED", "登录出现未识别的阻塞窗口，保留现场并停止自动重试")
        return any(w.root == w.hwnd and w.enabled and w.text.endswith(self.titles)
                   for w in snapshot.windows) and not self.dialogs(snapshot)

    def login(self) -> None:
        root, controls = self.prepare_login()
        # 账号 ComboBox 与其子 Edit 的几何距离可能相同；固定版本按原生编号定位。
        usernames = [w for w in controls if w.id == 1473 and w.class_name == "ComboBox" and w.parent == root]
        passwords = [w for w in controls if w.id == 7409 and w.password and w.class_name == "Edit"]
        if len(passwords) != 1 or len(usernames) != 1:
            raise BridgeError("SERVICE_NOT_READY", "登录输入控件身份不唯一，请人工核对")
        username = usernames[0]
        for window, secret in ((username, self.settings.account.username), (passwords[0], self.settings.account.password)):
            value = secret.get_secret_value().encode("gb18030").hex()
            self.native.ask(f"set_text {window.hwnd} {value}")
        verified = [w for w in self.native.windows().windows if w.hwnd == username.hwnd]
        if len(verified) != 1 or verified[0].text != self.settings.account.username.get_secret_value():
            raise BridgeError("SERVICE_NOT_READY", "登录账户输入未通过核对")
        buttons = [w for w in controls if w.class_name == "Button" and w.text.replace("&", "").replace(" ", "").startswith("登录")]
        if len(buttons) != 1:
            raise BridgeError("SERVICE_NOT_READY", "登录按钮身份不唯一")
        self.activate("用户登录");self.native.ask(f"focus {buttons[0].hwnd}");self.key("space")
        def ready() -> bool:
            return self.login_ready(self.native.managed_windows(), root)
        remaining = max(0, self.native.startup_deadline-time.monotonic()) if self.native.startup_deadline else 0
        self.wait(ready, "登录或文档确认超时，缺少明确连接故障证据，需要人工核对", remaining)
        self.baseline()
