"""标准下单板数值限价 IOC 填参；只创建手动预埋单，不按下单按钮。"""

import logging
from typing import Literal

from pydantic import BaseModel

from ..errors import BridgeError
from ..models import LimitOrder
from .gui import Gui
from .native import Window, Windows, decode

LOG = logging.getLogger(__name__)
TimeMode = Literal["GFD", "GIS", "FAK", "FOK"]


class FormState(BaseModel):
    instrument: str
    volume: str
    price: str
    time_mode: TimeMode
    side: Literal["buy", "sell"]
    offset: Literal["open", "close_today", "close"]


class MenuItem(BaseModel):
    index: int
    command_id: int
    enabled: bool
    text_hex: str


class Menu(BaseModel):
    items: list[MenuItem]


def mismatch(message: str) -> BridgeError:
    return BridgeError("TERMINAL_DATA_INVALID", message, 502)


class OrderForm:
    def __init__(self, gui: Gui):
        self.gui = gui

    def panel(self, snapshot: Windows) -> int:
        root = self.gui.main(snapshot).hwnd
        panels = [w.parent for w in snapshot.windows
                  if w.root == root and w.id == 3324 and w.class_name == "Button"
                  and w.text == "预埋/条件" and w.visible and w.enabled]
        if len(panels) != 1:
            raise mismatch("未找到唯一标准下单板")
        return panels[0]

    def control(self, identifier: int, kind: str, snapshot: Windows | None = None,
                *, actionable: bool = True) -> Window:
        snapshot = snapshot or self.gui.native.windows()
        panel = self.panel(snapshot)
        parents = {w.hwnd: w.parent for w in snapshot.windows}
        controls = [w for w in snapshot.windows if w.id == identifier and w.class_name == kind
                    and (not actionable or (w.visible and w.enabled)) and
                    (w.parent == panel or (kind == "Edit" and parents.get(w.parent) == panel))]
        if len(controls) != 1:
            raise mismatch(f"限价 IOC下单板控件 {identifier} 不唯一或不可用")
        return controls[0]

    def snapshot(self) -> FormState:
        snapshot = self.gui.baseline()
        for identifier in (3318, 3323, 3325):
            # 合约选定后套利/移仓等选项会隐藏或禁用；读取其状态不要求可点击。
            if self.control(identifier, "Button", snapshot, actionable=False).checked:
                raise mismatch("当前下单板启用了保值、套利或移仓模式，不能按普通投机单提交")
        if self.control(3321, "Button", snapshot).text != "开平":
            raise mismatch("限价 IOC下单板必须使用明确开平模式")
        sides = [(3310, "buy"), (3311, "sell")]
        offsets = [(3312, "open"), (3313, "close_today"), (3314, "close")]
        selected_sides = [value for key, value in sides if self.control(key, "Button", snapshot, actionable=False).checked == 1]
        selected_offsets = [value for key, value in offsets if self.control(key, "Button", snapshot, actionable=False).checked == 1]
        if len(selected_sides) != 1 or len(selected_offsets) != 1:
            raise mismatch("限价 IOC下单板买卖/开平选择不明确")
        return FormState.model_validate({
            "instrument": self.control(3301, "Edit", snapshot).text,
            "volume": self.control(3302, "Edit", snapshot).text,
            "price": self.control(3303, "Edit", snapshot).text,
            "time_mode": self.control(1472, "Static", snapshot).text,
            "side": selected_sides[0], "offset": selected_offsets[0],
        })

    def focus(self, window: Window) -> None:
        if self.gui.native.ask(f"focus {window.hwnd}").data.get("focused") is not True:
            raise BridgeError("GUI_RESET_FAILED", "限价 IOC填参控件未取得焦点")
        self.gui.touched = True

    def edit(self, identifier: int, text: str, *, skip_unchanged: bool = False) -> None:
        window = self.control(identifier, "Edit")
        if skip_unchanged and window.text == text:
            return
        self.focus(window)
        # 空串通过单个 NUL 表达；控制器参数本身不承载任意窗口或函数地址。
        encoded = text.encode("gb18030").hex() or "00"
        self.gui.native.ask(f"set_text {window.hwnd} {encoded}")
        self.gui.key("Tab")
        if self.control(identifier, "Edit").text != text:
            raise mismatch(f"限价 IOC填参控件 {identifier} 回读不一致")
        LOG.info("限价 IOC 填参控件 %s 已回读", identifier, extra={"step": "order_form"})

    def radio(self, identifier: int, label: str) -> None:
        window = self.control(identifier, "Button")
        if window.text != label:
            raise mismatch("买卖或开平控件标签不符合固定终端")
        if window.checked != 1:
            self.focus(window);self.gui.key("space")
        if self.control(identifier, "Button").checked != 1:
            raise mismatch("买卖或开平选项没有生效")

    def time_mode(self, value: TimeMode) -> None:
        window = self.control(1472, "Static")
        if window.text == value:
            return
        if window.text not in ("GFD", "GIS", "FAK", "FOK"):
            raise mismatch("未知的委托有效期")
        self.gui.activate()
        left, top, right, bottom = window.rect
        # 此 Static 通过鼠标打开菜单；坐标来自原生控件，不使用固定屏幕坐标。
        self.gui._xdo("mousemove", str((left+right)//2), str((top+bottom)//2), "click", "1")
        def menus() -> list[Window]:
            return [w for w in self.gui.native.windows().windows if w.class_name == "#32768" and w.visible]
        self.gui.wait(lambda: bool(menus()), "委托有效期菜单未打开")
        candidates = menus()
        if len(candidates) != 1:
            raise BridgeError("GUI_RESET_FAILED", "委托有效期菜单不唯一")
        try:
            menu = Menu.model_validate(self.gui.native.ask(f"menu {candidates[0].hwnd}").data)
            modes = ("GFD", "GIS", "FAK", "FOK")
            if (len(menu.items) != 4 or any(item.index != i or item.command_id != 40000+i
                    or not decode(item.text_hex).startswith(modes[i]+":")
                    for i, item in enumerate(menu.items))):
                raise mismatch("有效期菜单内容与固定终端不符")
            item = menu.items[modes.index(value)]
            if not item.enabled:
                raise mismatch("目标委托有效期不可选")
            self.gui.key("Home", *(["Down"]*item.index), "Return")
            self.gui.wait(lambda: not menus(), "委托有效期菜单未退出")
            if self.control(1472, "Static").text != value:
                raise mismatch("委托有效期回读不一致")
        finally:
            if not self.gui.native.poisoned and any(w.hwnd == candidates[0].hwnd for w in menus()):
                self.gui.key("Escape")

    def fill(self, request: LimitOrder) -> None:
        self.edit(3301, request.instrument_id)
        self.radio(3310 if request.side == "buy" else 3311, "买入" if request.side == "buy" else "卖出")
        self.radio(3312 if request.offset == "open" else 3314, "开仓" if request.offset == "open" else "平仓")
        self.edit(3302, str(request.volume))
        price = format(request.price.normalize(), "f")
        self.edit(3303, price)
        self.time_mode("FAK")
        expected = FormState(instrument=request.instrument_id, volume=str(request.volume), price=price,
                             time_mode="FAK", side=request.side, offset="open" if request.offset == "open" else "close")
        if self.snapshot() != expected:
            raise mismatch("限价 IOC下单板完整参数回读不一致")

    def open_preorder(self) -> Window:
        self.focus(self.control(3324, "Button"));self.gui.key("space")
        self.gui.wait(lambda: bool(self.gui.dialogs()), "手动预埋单设置窗口未出现")
        snapshot = self.gui.managed_snapshot()
        dialogs = self.gui.dialogs(snapshot)
        if (len(dialogs) != 1 or dialogs[0].text != "设置触发条件"
                or dialogs[0].parent != self.gui.main(snapshot).hwnd):
            raise BridgeError("GUI_RESET_FAILED", "限价 IOC预埋单出现未知窗口")
        return dialogs[0]

    def confirm_preorder(self, dialog: Window, request: LimitOrder) -> None:
        snapshot = self.gui.managed_snapshot()
        children = {w.id: w for w in snapshot.windows if w.parent == dialog.hwnd and w.visible}
        labels = {1: "确定", 2: "取消", 1278: "预埋(本地)，手动发出",
                  1279: "预埋(本地)，当重新进入交易状态时", 1280: "条件(本地)，当行情满足以下条件时"}
        if any(key not in children or children[key].class_name != "Button" or children[key].text != label
               for key, label in labels.items()):
            raise mismatch("手动预埋单设置控件不完整")
        if children[1278].checked != 1:
            self.focus(children[1278]);self.gui.key("space")
            snapshot = self.gui.native.windows()
            children = {w.id: w for w in snapshot.windows if w.parent == dialog.hwnd and w.visible}
        price = format(request.price.normalize(), "f")
        summary = f"{request.instrument_id} {'买' if request.side == 'buy' else '卖'} {'开仓' if request.offset == 'open' else '平仓'}{request.volume}手,价格:{price}"
        if (children[1278].checked != 1 or children[1279].checked or children[1280].checked
                or 4220 not in children or children[4220].text != summary):
            raise mismatch("手动预埋单摘要不是本次限价 IOC参数，停止创建")
        self.focus(children[1]);self.gui.key("space")
        self.gui.wait(lambda: all(w.hwnd != dialog.hwnd for w in self.gui.dialogs()), "手动预埋单窗口未退出")

    def restore(self, state: FormState) -> None:
        self.edit(3301, state.instrument, skip_unchanged=True)
        self.radio(3310 if state.side == "buy" else 3311, "买入" if state.side == "buy" else "卖出")
        identifier, label = {"open": (3312, "开仓"), "close": (3314, "平仓"), "close_today": (3313, "平今")}[state.offset]
        self.radio(identifier, label)
        self.edit(3302, state.volume, skip_unchanged=True)
        self.edit(3303, state.price, skip_unchanged=True)
        self.time_mode(state.time_mode)
        if self.snapshot() != state:
            raise BridgeError("GUI_RESET_FAILED", "限价 IOC下单板收尾回读不一致")
