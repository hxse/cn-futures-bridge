"""CSV 导入和回读验证参数，交易发送与撤单只走已核对目标的快捷键。"""

from collections.abc import Callable
import csv
from decimal import Decimal
import logging
import re

from ..errors import BridgeError
from ..models import CancelByExchange, LimitOrder
from ..results import SubmissionResult
from .csv_data import PREORDER_HEADER, number
from .gui import Gui
from .native import InstrumentInfo, decode
from .steps import Steps

LOG = logging.getLogger(__name__)
Rows = list[dict[str, str]]
ReadTable = Callable[[str, Steps], Rows]


def same_parameters(row: dict[str, str], request: LimitOrder) -> bool:
    return (row["类型"] == "预埋单(手动)" and row["合约"] == request.instrument_id
            and row["买卖"].strip() == ("买" if request.side == "buy" else "卖")
            and row["开平"] == ("开仓" if request.offset == "open" else "平仓")
            and number(row["报单价格"]) == request.price and number(row["报单手数"]) == request.volume
            and row["投保"] == "投机")


def local_identity(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[key] for key in PREORDER_HEADER if key not in ("状态", "详细状态"))


class OrderActions:
    def __init__(self, gui: Gui, read: ReadTable):
        self.gui = gui
        self.read = read

    def limit(self, request: LimitOrder, info: InstrumentInfo, steps: Steps) -> SubmissionResult:
        tick = Decimal(str(info.tick))
        if tick <= 0:
            raise BridgeError("SERVICE_NOT_READY", "合约最小变动价位尚未就绪")
        if request.price % tick:
            raise BridgeError("INVALID_ARGUMENTS", "价格不是最小变动价位的整数倍", 422)
        if (info.lower > 0 and request.price < Decimal(str(info.lower))) or (
                info.upper > 0 and request.price > Decimal(str(info.upper))):
            raise BridgeError("INVALID_ARGUMENTS", "价格超出终端当前涨跌停范围", 422)
        before = self.read("preorders", steps)
        if any(same_parameters(row, request) for row in before):
            raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "已存在相同参数的本地预埋单，不能唯一归属新记录", 409)
        path = steps.path("input")
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
            matches = [r for r in after if same_parameters(r, request)]
            if message == "读取 1 条，导入成功 0 条" and after == before:
                steps.save("rejected", effect="rejected")
                raise BridgeError("ORDER_REJECTED", "终端拒绝导入，未发送委托", 422, submission_status="rejected")
            if (len(matches) != 1 or len(after) != len(before)+1
                    or matches[0]["状态"] != "未启动" or matches[0]["触发条件"] != "手动发出"
                    or sorted(local_identity(r) for r in after if r is not matches[0])
                    != sorted(local_identity(r) for r in before)):
                raise BridgeError("OPERATION_STATUS_UNKNOWN", "导入回读不能证明本次记录的唯一身份", 502)
            steps.owned_row = matches[0]
            steps.save("parameters_verified")
        with steps.step("locate"):
            self.select_local(steps)
        with steps.step("send"):
            steps.save("submitting", effect="unknown")
            self.gui.key("alt+q")
            # 读取一次本地状态，不轮询柜台接受或成交。
            sent = self.owned(self.read("preorders", steps), steps)
            if sent[1]["状态"] != "已发送":
                raise BridgeError("OPERATION_STATUS_UNKNOWN", "快捷键已触发，但本地已发送状态未确认", 502)
            steps.save("submitted", effect="submitted")
        self.observe_orders(steps)
        return SubmissionResult(request_id=steps.request_id)

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

    def observe_orders(self, steps: Steps) -> None:
        with steps.step("observe_orders_once"):
            try:
                rows = self.read("orders", steps)
                LOG.info("提交后订单快照 %d 条；未凭相似参数认领订单编号", len(rows),
                         extra={"request_id": steps.request_id, "step": "observe_orders_once"})
            except BridgeError as exc:
                # 单次快照失败不抹去已持久化的本地提交事实；收尾仍必须验证。
                LOG.warning("本地提交后快照失败：%s", exc.code, extra={"request_id": steps.request_id})

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
            # 设置被人工改变时 Delete 可能直接发送；在按键前就持久化不确定副作用。
            steps.save("submitting", effect="unknown")
            self.gui.key("Delete")
            self.gui.wait(lambda: bool(self.gui.dialogs()), "未取得撤单确认，不能判断是否已发送")
            snapshot = self.gui.native.windows()
            dialogs = self.gui.dialogs(snapshot)
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
            self.gui.baseline()
            steps.save("submitted", effect="submitted")
        self.observe_orders(steps)
        return SubmissionResult(request_id=steps.request_id, order_id=request.order_sys_id)
