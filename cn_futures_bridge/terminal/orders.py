"""CSV 导入和回读验证参数，交易发送与撤单只走已核对目标的快捷键。"""

from collections.abc import Callable
import csv
from decimal import Decimal
import logging
import re
import time

from ..errors import BridgeError
from ..models import CancelByExchange, LimitOrder, MarketOrder
from ..results import SubmissionResult
from .csv_data import PREORDER_HEADER, number
from .gui import Gui
from .native import InstrumentInfo, decode
from .steps import Steps
from .tracking import Receipt, parked_snapshot, validate_preorder

LOG = logging.getLogger(__name__)
Rows = list[dict[str, str]]
ReadTable = Callable[[str, Steps], Rows]


def same_parameters(row: dict[str, str], request: MarketOrder) -> bool:
    if not (row["类型"] == "预埋单(手动)" and row["合约"] == request.instrument_id
            and row["买卖"].strip() == ("买" if request.side == "buy" else "卖")
            and row["开平"] == ("开仓" if request.offset == "open" else "平仓")
            and number(row["报单手数"]) == request.volume and row["投保"] == "投机"):
        return False
    price = row["报单价格"].strip()
    market_price = bool(re.fullmatch(r"市价(?:/\d+(?:\.\d+)?)?", price))
    return (not market_price and number(price) == request.price) if isinstance(request, LimitOrder) else market_price


def local_identity(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[key] for key in PREORDER_HEADER if key not in ("状态", "详细状态"))


def validate_limit(request: LimitOrder, info: InstrumentInfo) -> None:
    tick = Decimal(str(info.tick))
    if not tick.is_finite() or tick <= 0:
        raise BridgeError("SERVICE_NOT_READY", "合约最小变动价位尚未就绪")
    if info.limit_min_volume < 1 or info.limit_max_volume < info.limit_min_volume:
        raise BridgeError("SERVICE_NOT_READY", "限价最小/最大手数资料无效")
    if not info.limit_min_volume <= request.volume <= info.limit_max_volume:
        raise BridgeError("INVALID_ARGUMENTS", "手数超出终端限价手数范围，不自动拆单", 422)
    if request.price % tick:
        raise BridgeError("INVALID_ARGUMENTS", "价格不是最小变动价位的整数倍", 422)
    if (info.lower > 0 and request.price < Decimal(str(info.lower))) or (
            info.upper > 0 and request.price > Decimal(str(info.upper))):
        raise BridgeError("INVALID_ARGUMENTS", "价格超出终端当前涨跌停范围", 422)


class OrderActions:
    def __init__(self, gui: Gui, read: ReadTable):
        self.gui = gui
        self.read = read

    def limit(self, request: LimitOrder, info: InstrumentInfo, steps: Steps) -> SubmissionResult:
        validate_limit(request, info)
        before = self.read("preorders", steps)
        if any(same_parameters(row, request) for row in before):
            raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "已存在相同参数的本地预埋单，不能唯一归属新记录", 409)
        path = steps.path("input")
        cursor = parked_snapshot(self.gui.native).last_id
        row = ["预埋单(手动)", "未启动", "手动发出", request.instrument_id, decode(info.name_hex),
               "买　" if request.side == "buy" else "　卖", "开仓" if request.offset == "open" else "平仓",
               str(request.price), str(request.volume), "投机", "13:00:00", ""]
        with steps.step("import"):
            with path.open("w", encoding="gb18030", newline="") as stream:
                writer = csv.writer(stream);writer.writerow(PREORDER_HEADER);writer.writerow(row)
            window = self.gui.grid("preorders")
            steps.save("importing", effect="unknown")
            message = self.gui.native.import_csv(window.hwnd, path)
            LOG.info("原生导入结果：%s", message, extra={"request_id": steps.request_id, "step": "import"})
        with steps.step("verify_parameters"):
            after = self.read("preorders", steps)
            if message == "读取 1 条，导入成功 0 条" and after == before:
                steps.save("rejected", effect="rejected")
                raise BridgeError("ORDER_REJECTED", "终端拒绝导入，未发送委托", 422, submission_status="rejected")
            self.bind_local(before, after, request, steps, ("未启动",))
            steps.parked_id = validate_preorder(request, parked_snapshot(self.gui.native, cursor))
        return self.send_local(request, steps)

    def bind_local(self, before: Rows, after: Rows, request: MarketOrder, steps: Steps,
                   statuses: tuple[str, ...]) -> None:
        matches = [r for r in after if same_parameters(r, request)]
        if (len(matches) != 1 or len(after) != len(before)+1
                or matches[0]["状态"] not in statuses or matches[0]["触发条件"] != "手动发出"
                or sorted(local_identity(r) for r in after if r is not matches[0])
                != sorted(local_identity(r) for r in before)):
            raise BridgeError("OPERATION_STATUS_UNKNOWN", "CSV 回读不能证明本次记录的唯一身份", 502)
        steps.owned_row = matches[0]
        steps.save("parameters_verified")

    def finish_capture(self, steps: Steps) -> Receipt | None:
        if not steps.capture_armed:
            return None
        result = Receipt.model_validate(self.gui.native.ask("receipt finish").data)
        if result.armed or result.active:
            self.gui.native.poisoned = True
            raise BridgeError("GUI_RESET_FAILED", "发送观察入口未完全恢复")
        steps.capture_armed = False
        return result

    def send_local(self, request: LimitOrder, steps: Steps) -> SubmissionResult:
        with steps.step("locate"):
            self.select_local(steps)
        with steps.step("send"):
            assert steps.parked_id is not None and steps.session is not None
            self.gui.native.ask(f"receipt arm {steps.parked_id}")
            steps.capture_armed = True
            steps.save("submitting", effect="unknown")
            self.gui.key("alt+q")
            self.gui.drain_notices()
            receipt = self.finish_capture(steps)
            assert receipt is not None
            steps.identity = receipt.identity(request, steps.parked_id, steps.session)
            steps.save("identity_captured")
            LOG.info("已捕获实际发送引用：%s", steps.identity.model_dump_json(),
                     extra={"request_id": steps.request_id, "step": "capture_identity"})
            sent = self.owned(self.read("preorders", steps), steps)
            if sent[1]["状态"] != "已发送":
                raise BridgeError("OPERATION_STATUS_UNKNOWN", "快捷键已触发，但本地已发送状态未确认", 502)
            steps.save("submitted", effect="submitted")
        return SubmissionResult(request_id=steps.request_id, identity=steps.identity)

    def owned(self, rows: Rows, steps: Steps) -> tuple[int, dict[str, str]]:
        if steps.owned_row is None:
            raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "本次预埋记录身份未建立", 409)
        matches = [(i, row) for i, row in enumerate(rows) if local_identity(row) == local_identity(steps.owned_row)]
        if len(matches) != 1:
            raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "本次预埋记录缺失或出现重复", 409)
        return matches[0]

    def select_local(self, steps: Steps) -> None:
        rows = self.read("preorders", steps)
        index, row = self.owned(rows, steps)
        self.gui.select("preorders", self.gui.grid("preorders"), index)
        fresh = self.read("preorders", steps)
        if self.owned(fresh, steps)[0] != index or fresh[index] != row:
            raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "选择后本地记录已变化，停止操作", 409)
        self.gui.native.select(self.gui.grid("preorders").hwnd, index)

    def cleanup(self, steps: Steps) -> None:
        if steps.owned_row is None:
            return
        self.select_local(steps)
        self.gui.key("alt+x")
        identity = local_identity(steps.owned_row)
        if any(local_identity(row) == identity for row in self.read("preorders", steps)):
            raise BridgeError("GUI_RESET_FAILED", "本次本地预埋记录未能清理")
        steps.owned_row = None

    def cancel(self, request: CancelByExchange, steps: Steps) -> SubmissionResult:
        rows = self.read("working", steps)
        matches = [(i, row) for i, row in enumerate(rows)
                   if row["报单编号"] == request.order_sys_id and row["合约"] == request.instrument_id]
        if len(matches) != 1:
            raise BridgeError("ORDER_NOT_FOUND" if not matches else "ORDER_IDENTITY_AMBIGUOUS",
                              "未找到唯一的目标活动订单", 409)
        index, row = matches[0]
        with steps.step("locate"):
            self.gui.select("working", self.gui.grid("working"), index)
            fresh = self.read("working", steps)
            if index >= len(fresh) or fresh[index] != row:
                raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "撤单选择后目标记录已变化", 409)
            self.gui.native.select(self.gui.grid("working").hwnd, index)
        with steps.step("cancel_confirmation"):
            # 关闭确认设置时 Delete 会直接发送；确认只能来自后续 CSV。
            steps.save("submitting", effect="unknown")
            self.gui.key("Delete")
            deadline = time.monotonic() + min(.5, self.gui.timeout)
            snapshot = self.gui.managed_snapshot()
            while not self.gui.dialogs(snapshot) and time.monotonic() < deadline:
                time.sleep(self.gui.interval)
                snapshot = self.gui.managed_snapshot()
            dialogs = self.gui.dialogs(snapshot)
            if not dialogs:
                return SubmissionResult(request_id=steps.request_id, order_id=request.order_sys_id)
            if len(dialogs) != 1 or not dialogs[0].text.startswith("确认"):
                raise BridgeError("GUI_RESET_FAILED", "撤单出现未知确认窗口")
            dialog = dialogs[0]
            text = "\n".join(w.text for w in snapshot.windows if w.root == dialog.hwnd)
            identifier = request.order_sys_id.strip()
            if ("撤" not in text or request.instrument_id not in text
                    or not re.search(r"(?<![A-Za-z0-9])"+re.escape(identifier)+r"(?![A-Za-z0-9])", text)):
                raise BridgeError("GUI_RESET_FAILED", "撤单确认未包含已核对的合约和订单编号")
            self.gui.activate(dialog.text);self.gui.key("Return")
            self.gui.wait(lambda: all(w.hwnd != dialog.hwnd for w in self.gui.dialogs()), "撤单确认未退出")
            self.gui.ensure_ready()
            steps.save("submitted", effect="submitted")
        return SubmissionResult(request_id=steps.request_id, order_id=request.order_sys_id)
