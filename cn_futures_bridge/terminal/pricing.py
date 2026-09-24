"""价格先分类再对齐；精确整数价位运算不受 Decimal 取余精度限制。"""

from decimal import Decimal
from fractions import Fraction
import math

from ..errors import BridgeError, FieldProblem
from ..models import LimitOrder
from ..results import PriceAdjustmentReason, PriceContext
from .native import InstrumentInfo


def price_error(message: str, info: InstrumentInfo, price: Decimal | None = None,
                ratio: float | None = None, *, status: int = 422) -> BridgeError:
    def known(value: float) -> float | None:
        return value if math.isfinite(value) and value > 0 else None
    context = PriceContext(requested_price=float(price) if price is not None else None,
        price_tick=known(info.tick), lower_limit=known(info.lower), upper_limit=known(info.upper),
        max_deviation_ratio=ratio)
    return BridgeError("SERVICE_NOT_READY" if status == 503 else "INVALID_ARGUMENTS", message, status,
        details=[FieldProblem(loc=["body", "price"], type="price_rules_unavailable" if status == 503 else "invalid_price",
                              message=message, context=context)])


def price_rules(info: InstrumentInfo, price: Decimal | None = None,
                ratio: float | None = None) -> tuple[Decimal, Decimal, Decimal]:
    values = (info.tick, info.lower, info.upper)
    if (not info.found or not info.data_ready or any(not math.isfinite(v) or v <= 0 for v in values)
            or info.lower > info.upper):
        raise price_error("合约报价步长或涨跌停资料无效，停止下单", info, price, ratio, status=503)
    tick, lower, upper = (Decimal(str(value)) for value in values)
    if math.ceil(Fraction(lower) / Fraction(tick)) > math.floor(Fraction(upper) / Fraction(tick)):
        raise price_error("合约涨跌停区间内没有合法报价", info, price, ratio, status=503)
    return tick, lower, upper


def validate_volume(request: LimitOrder, info: InstrumentInfo) -> None:
    if info.limit_min_volume < 1 or info.limit_max_volume < info.limit_min_volume:
        raise BridgeError("SERVICE_NOT_READY", "限价最小/最大手数资料无效")
    if not info.limit_min_volume <= request.volume <= info.limit_max_volume:
        raise BridgeError("INVALID_ARGUMENTS", "手数超出终端限价手数范围，不自动拆单", 422)


def validate_limit(request: LimitOrder, info: InstrumentInfo) -> None:
    """在交易入口严格复核实际价；不在此处隐式再次调整。"""
    tick, lower, upper = price_rules(info, request.price)
    validate_volume(request, info)
    if not lower <= request.price <= upper:
        raise price_error("实际价格超出终端当前涨跌停范围", info, request.price)
    if (Fraction(request.price) / Fraction(tick)).denominator != 1:
        raise price_error("实际价格不是最小变动价位的整数倍", info, request.price)
    if Decimal(str(float(request.price))) != request.price:
        raise price_error("实际价格不能在当前终端数值传输中准确表示", info, request.price)


def tick_multiple(units: int, tick: Decimal) -> Decimal:
    # 直接构造十进制系数及指数，避免乘法受调用方的 Decimal context 舍入。
    parts = tick.as_tuple()
    assert isinstance(parts.exponent, int)  # price_rules 已保证 tick 有限。
    coefficient = 0
    for digit in parts.digits:
        coefficient = coefficient * 10 + digit
    digits = tuple(int(digit) for digit in str(units * coefficient))
    return Decimal((0, digits, parts.exponent))


def normalize_limit(request: LimitOrder, info: InstrumentInfo,
                    max_deviation_ratio: float) -> tuple[LimitOrder, list[PriceAdjustmentReason]]:
    """比例来自已校验的配置；仅生成执行副本，不修改原请求或发送委托。"""
    tick, lower, upper = price_rules(info, request.price, max_deviation_ratio)
    validate_volume(request, info)
    quantum, low, high = Fraction(tick), Fraction(lower), Fraction(upper)
    epsilon = quantum / 1_000_000_000
    price = Fraction(request.price)
    reasons: list[PriceAdjustmentReason] = []
    scaled = price / quantum
    floor = math.floor(scaled)
    nearest = (floor + int(scaled-floor >= Fraction(1, 2))) * quantum
    if price != nearest and abs(price-nearest) <= epsilon:
        price = nearest
        reasons.append("float_noise")
    ratio = Fraction(str(max_deviation_ratio))
    if request.side == "buy":
        if price < low:
            raise price_error("买入目标价低于跌停，不自动提高最高买价", info, request.price, max_deviation_ratio)
        if price > high:
            excess = price - high * (1+ratio)
            if excess > epsilon:
                raise price_error("买价超过允许自动截断的涨停上方幅度", info, request.price, max_deviation_ratio)
            if excess > 0 and "float_noise" not in reasons:
                reasons.append("float_noise")
            price = high
            reasons.append("upper_limit")
        units = math.floor(price / quantum)
        direction: PriceAdjustmentReason = "tick_floor"
    else:
        if price > high:
            raise price_error("卖出目标价高于涨停，不自动降低最低卖价", info, request.price, max_deviation_ratio)
        if price < low:
            excess = low * (1-ratio) - price
            if excess > epsilon:
                raise price_error("卖价超过允许自动截断的跌停下方幅度", info, request.price, max_deviation_ratio)
            if excess > 0 and "float_noise" not in reasons:
                reasons.append("float_noise")
            price = low
            reasons.append("lower_limit")
        units = math.ceil(price / quantum)
        direction = "tick_ceil"
    actual = units * quantum
    if actual != price:
        reasons.append(direction)
    if actual <= 0 or not low <= actual <= high:
        raise price_error("按买卖方向对齐后没有符合目标价与涨跌停的合法报价", info, request.price, max_deviation_ratio)
    effective = request.model_copy(update={"price": tick_multiple(units, tick)})
    try:
        validate_limit(effective, info)
    except BridgeError as error:
        raise price_error(str(error), info, request.price, max_deviation_ratio, status=error.status) from error
    return effective, reasons
