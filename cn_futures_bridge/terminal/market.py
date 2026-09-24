"""限价 IOC 与市价模拟共用下单板、CSV 核对和唯一发送链路。"""

from decimal import Decimal
import logging

from ..errors import BridgeError
from ..models import LimitOrder, MarketOrder
from ..results import OrderExecution, SubmissionResult
from .order_form import OrderForm
from .native import InstrumentInfo
from .orders import OrderActions, same_parameters, validate_limit
from .steps import Steps
from .tracking import ParkedSnapshot, parked_snapshot, validate_preorder

LOG = logging.getLogger(__name__)


def emulate_market(request: MarketOrder, info: InstrumentInfo) -> LimitOrder:
    if info.status != 3:
        raise BridgeError("MARKET_NOT_TRADING", "限价模拟市价仅在终端确认连续交易时提交", 409)
    price = Decimal(str(info.upper if request.side == "buy" else info.lower))
    if not price.is_finite() or price <= 0 or info.lower <= 0 or info.upper < info.lower:
        raise BridgeError("SERVICE_NOT_READY", "缺少有效涨跌停保护价格，停止模拟市价")
    limit = LimitOrder.model_validate({**request.model_dump(), "price": price, "time_in_force": "IOC"})
    validate_limit(limit, info)
    return limit


class MarketActions:
    def __init__(self, orders: OrderActions):
        self.orders = orders
        self.gui = orders.gui
        self.form = OrderForm(self.gui)

    def snapshot(self, after: int = -1) -> ParkedSnapshot:
        return parked_snapshot(self.gui.native, after)

    def create(self, request: MarketOrder, info: InstrumentInfo, steps: Steps) -> SubmissionResult:
        limit = emulate_market(request, info)
        steps.execution = OrderExecution(kind="emulated_market", price=float(limit.price), time_in_force="IOC")
        LOG.info("市价模拟采用限价 IOC，实际限价 %s", limit.price,
                 extra={"request_id": steps.request_id, "step": "emulate_market"})
        return self.limit_ioc(limit, info, steps)

    def limit_ioc(self, request: LimitOrder, info: InstrumentInfo, steps: Steps) -> SubmissionResult:
        validate_limit(request, info)
        before = self.orders.read("preorders", steps)
        if any(same_parameters(row, request) for row in before):
            raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "已有相同参数的限价预埋单，不能唯一归属新记录", 409)
        with steps.step("fill_ioc_form"):
            steps.form_state = self.form.snapshot()
            self.form.fill(request)
        with steps.step("create_ioc_preorder"):
            cursor = self.snapshot().last_id
            steps.market_dialog = self.form.open_preorder()
            steps.save("importing", effect="unknown")
            self.form.confirm_preorder(steps.market_dialog, request)
            steps.market_dialog = None
        with steps.step("verify_ioc_parameters"):
            after = self.orders.read("preorders", steps)
            self.orders.bind_local(before, after, request, steps, ("未启动", "已启动"))
            native = self.snapshot(cursor)
            LOG.info("限价预埋单原生条件：%s", native.model_dump_json(),
                     extra={"request_id": steps.request_id, "action": steps.action, "step": "verify_ioc_parameters"})
            steps.parked_id = validate_preorder(request, native)
        return self.orders.send_local(request, steps, after)

    def close_dialog(self, steps: Steps) -> None:
        if steps.market_dialog is not None:
            if any(w.hwnd == steps.market_dialog.hwnd for w in self.gui.dialogs()):
                self.gui.close_dialog(steps.market_dialog)
            steps.market_dialog = None

    def restore(self, steps: Steps) -> None:
        if steps.form_state is not None:
            self.form.restore(steps.form_state)
            steps.form_state = None
