"""能力边界在入队前和执行前复核，未核验模式不以近似交易代替。"""

from ..config import Settings
from ..errors import BridgeError, Capability, initial_capabilities
from ..models import BalanceQuery, CancelBySession, LimitOrder, MarketOrder, RequestModel

EXCHANGE_NUMBERS = {"SHFE": 1, "DCE": 2, "CZCE": 3, "CFFEX": 4, "INE": 5, "GFEX": 6}


def capabilities() -> dict[str, Capability]:
    result = initial_capabilities()
    for name in ("create_limit_order", "cancel_order", "fetch_orders", "fetch_trades", "fetch_positions",
                 "fetch_balance", "fetch_trading_status"):
        result[name] = "supported"
    result["invest_unit"] = "unsupported"
    result["non_cny_balance"] = "unsupported"
    return result


def validate_capability(request: RequestModel, settings: Settings) -> None:
    if request.mode != settings.request_mode:
        raise BridgeError("ENVIRONMENT_MISMATCH",
                          f"请求环境为 {request.mode}，当前启动环境为 {settings.request_mode}；不会自动切换", 409)
    if getattr(request, "invest_unit_id", ""):
        raise BridgeError("CAPABILITY_NOT_SUPPORTED", "终端不能可靠区分投资单元", 501)
    if isinstance(request, BalanceQuery) and request.currency_id != "CNY":
        raise BridgeError("CAPABILITY_NOT_SUPPORTED", "当前只支持人民币资金", 501)
    if isinstance(request, CancelBySession):
        raise BridgeError("CAPABILITY_NOT_SUPPORTED", "会话编号撤单尚未核验", 501)
    if isinstance(request, MarketOrder):
        if not isinstance(request, LimitOrder):
            raise BridgeError("CAPABILITY_NOT_SUPPORTED", "原生市价模式尚未核验", 501)
        if request.time_in_force != "GFD":
            raise BridgeError("CAPABILITY_NOT_SUPPORTED", "IOC/FOK 模式尚未核验", 501)
        if request.offset in ("close_today", "close_yesterday"):
            raise BridgeError("CAPABILITY_NOT_SUPPORTED", "指定今昨仓的闭环尚未核验", 501)
        if request.hedge_flag != "speculation":
            raise BridgeError("CAPABILITY_NOT_SUPPORTED", "非投机报单尚未核验", 501)
