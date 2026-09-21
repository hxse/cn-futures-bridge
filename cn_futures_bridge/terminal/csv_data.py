"""解析快期原生 GB18030 CSV，完整读取失败不伪装成空结果。"""

import csv
from decimal import Decimal, InvalidOperation
import io
from pathlib import Path
import re
from typing import Literal

from ..errors import BridgeError
from ..models import Exchange, OrderQuery, PositionQuery, TradeQuery
from ..results import Balance, Order, Position, ReturnedOffset, Trade

EXCHANGES: dict[str, Exchange] = {
    "SHFE": "SHFE", "上海": "SHFE", "上期所": "SHFE", "上海期货交易所": "SHFE",
    "INE": "INE", "能源": "INE", "上期能源": "INE", "上海国际能源交易中心": "INE",
    "DCE": "DCE", "大连": "DCE", "大商所": "DCE", "大连商品交易所": "DCE",
    "CZCE": "CZCE", "郑州": "CZCE", "郑商所": "CZCE", "郑州商品交易所": "CZCE",
    "CFFEX": "CFFEX", "中金": "CFFEX", "中金所": "CFFEX", "中国金融期货交易所": "CFFEX",
    "GFEX": "GFEX", "广州": "GFEX", "广期所": "GFEX", "广州期货交易所": "GFEX",
}
OFFSETS: dict[str, ReturnedOffset] = {"开仓": "open", "平仓": "close", "平今": "close_today", "平昨": "close_yesterday"}
PREORDER_HEADER = ["类型", "状态", "触发条件", "合约", "合约名", "买卖", "开平", "报单价格", "报单手数", "投保", "预埋时间", "详细状态"]
REQUIRED = {
    "orders": {"报单编号", "合约", "买卖", "开平", "挂单状态", "报单价格", "报单手数", "未成交手数", "成交手数", "详细状态", "报单时间", "交易所"},
    "trades": {"成交编号", "合约", "买卖", "开平", "成交价格", "成交手数", "成交时间", "交易所"},
    "positions": {"合约", "买卖", "总持仓", "昨仓", "今仓", "可平量", "交易所"},
    "working": {"报单编号", "合约", "买卖", "开平", "报单手数", "未成交手数", "报单价格", "报单时间"},
    "preorders": set(PREORDER_HEADER),
}


def invalid(message: str) -> BridgeError:
    return BridgeError("TERMINAL_DATA_INVALID", message, 502)


def read_csv(path: Path, table: str, maximum: int) -> list[dict[str, str]]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > maximum:
        raise invalid("CSV 文件缺失或超过容量限制")
    try:
        text = path.read_bytes().decode("gb18030")
        if not text.endswith(("\n", "\r")):
            raise invalid("CSV 未完整结束")
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeError, csv.Error) as exc:
        raise invalid("CSV 编码或结构无效") from exc
    if not records:
        raise invalid("CSV 缺少表头")
    header = [name.lstrip("\ufeff") for name in records[0]]
    if len(set(header)) != len(header) or not REQUIRED[table].issubset(header):
        raise invalid("CSV 表头不符合固定终端契约")
    rows: list[dict[str, str]] = []
    for record in records[1:]:
        if not record:
            continue
        if len(record) != len(header):
            raise invalid("CSV 行列数量不完整")
        rows.append(dict(zip(header, record)))
    return rows


def number(value: str | None, *, optional: bool = False) -> Decimal | None:
    value = value.strip() if value is not None else ""
    if value in ("", "-", "--") and optional:
        return None
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        raise invalid("CSV 必需数值无效")
    try:
        result = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise invalid("CSV 数值无法解析") from exc
    if not result.is_finite():
        raise invalid("CSV 包含非有限数值")
    return result


def amount(row: dict[str, str], key: str, *, optional: bool = True) -> float | None:
    value = number(row.get(key), optional=optional)
    return float(value) if value is not None else None


def volume(row: dict[str, str], key: str, *, optional: bool = False) -> int | None:
    value = number(row.get(key), optional=optional)
    if value is None:
        return None
    if value < 0 or value != value.to_integral_value():
        raise invalid("CSV 手数不是非负整数")
    return int(value)


def required_volume(row: dict[str, str], key: str) -> int:
    result = volume(row, key)
    if result is None:
        raise invalid("CSV 缺少手数")
    return result


def identity(row: dict[str, str]) -> tuple[Exchange, str, Literal["buy", "sell"]]:
    exchange = EXCHANGES.get(row.get("交易所", "").strip())
    instrument = row.get("合约", "").strip()
    direction = row.get("买卖", "").strip()
    if exchange is None or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,79}", instrument) or direction not in ("买", "卖"):
        raise invalid("CSV 交易所、合约或方向身份无效")
    return exchange, instrument, "buy" if direction == "买" else "sell"


def order_row(row: dict[str, str]) -> Order:
    exchange, instrument, side = identity(row)
    total = required_volume(row, "报单手数")
    filled = required_volume(row, "成交手数")
    remaining = required_volume(row, "未成交手数")
    if filled > total or remaining > total:
        raise invalid("委托数量关系无效")
    raw = row["挂单状态"].strip()
    message = row.get("详细状态", "").strip()
    status: Literal["pending", "open", "partially_filled", "filled", "cancelled", "rejected", "unknown"]
    if raw in ("错单", "废单", "拒绝") or any(word in message for word in ("不足", "禁止", "拒绝", "报单失败", "不允许", "无效", "不合法")):
        status = "rejected"
    elif "撤" in raw:
        status = "cancelled"
    elif total > 0 and filled == total:
        status = "filled"
    elif filled > 0 and remaining > 0:
        status = "partially_filled"
    elif raw in ("未成交", "未成", "排队中", "已报", "挂单中"):
        status = "open"
    elif not raw or raw in ("报单中", "待报", "未报"):
        status = "pending"
    else:
        status = "unknown"
    return Order(order_id=row["报单编号"] or None, exchange_id=exchange, instrument_id=instrument,
                 side=side, offset=OFFSETS.get(row["开平"].strip(), "unknown"), volume=total,
                 filled_volume=filled, remaining_volume=remaining, price=amount(row, "报单价格"),
                 status=status, status_message=message or None, order_time=row["报单时间"] or None)


def trade_row(row: dict[str, str]) -> Trade:
    exchange, instrument, side = identity(row)
    if not row["成交编号"].strip():
        raise invalid("成交缺少编号")
    price = amount(row, "成交价格", optional=False)
    assert price is not None
    return Trade(trade_id=row["成交编号"], exchange_id=exchange, instrument_id=instrument, side=side,
                 offset=OFFSETS.get(row["开平"].strip(), "unknown"), volume=required_volume(row, "成交手数"),
                 price=price, trade_time=row["成交时间"] or None,
                 commission=amount(row, "手续费"), close_profit=amount(row, "平仓盈亏(逐笔)"))


def position_row(row: dict[str, str]) -> Position:
    exchange, instrument, side = identity(row)
    hedge: Literal["speculation", "arbitrage", "hedge", "unknown"] = "unknown"
    if row.get("投保", "").strip() == "投机": hedge = "speculation"
    elif row.get("投保", "").strip() == "套利": hedge = "arbitrage"
    elif row.get("投保", "").strip() in ("保值", "套保"): hedge = "hedge"
    return Position(exchange_id=exchange, instrument_id=instrument, direction="long" if side == "buy" else "short",
                    hedge_flag=hedge, volume=required_volume(row, "总持仓"), today_volume=volume(row, "今仓", optional=True),
                    yesterday_volume=volume(row, "昨仓", optional=True), available_volume=volume(row, "可平量", optional=True),
                    average_price=amount(row, "持仓均价"), margin=amount(row, "实收保证金"), profit=amount(row, "持仓盈亏"))


def matches(row: Order | Trade | Position, query: PositionQuery) -> bool:
    if query.exchange_id and row.exchange_id != query.exchange_id: return False
    if query.instrument_id and row.instrument_id != query.instrument_id: return False
    if isinstance(query, OrderQuery) and isinstance(row, Order):
        if query.order_sys_id and row.order_id != query.order_sys_id: return False
        return in_interval(row.order_time, query.insert_time_start, query.insert_time_end)
    if isinstance(query, TradeQuery) and isinstance(row, Trade):
        if query.trade_id and row.trade_id != query.trade_id: return False
        return in_interval(row.trade_time, query.trade_time_start, query.trade_time_end)
    return True


def in_interval(value: str | None, start: str | None, end: str | None) -> bool:
    if not start and not end: return True
    if not value or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d", value):
        raise invalid("缺少时间过滤所需的有效字段")
    return (not start or value >= start) and (not end or value <= end)


def funds_text(text: str) -> Balance:
    if "服务器" not in text or "本地" not in text:
        raise invalid("资金详情缺少服务器/本地列来源")
    labels = {"动态权益": "equity", "可用资金": "available", "占用保证金": "margin",
              "冻结保证金": "frozen_margin", "冻结手续费": "frozen_commission", "手续费": "commission"}
    values: dict[str, object] = {"currency_id": "CNY", "values_source": "server"}
    for label, field in labels.items():
        lines = [line for line in text.splitlines() if re.match(r"^\s*[=+\-]?\s*" + label + "：", line)]
        if len(lines) != 1:
            if field in ("equity", "available", "margin"): raise invalid("资金详情缺少必需字段")
            values[field] = None;continue
        columns = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", lines[0])
        if len(columns) != 2: raise invalid("资金详情列结构无效")
        parsed = number(columns[0]);assert parsed is not None
        values[field] = float(parsed)
    return Balance.model_validate(values)
