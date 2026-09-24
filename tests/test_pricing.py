"""价格分类、精确价位运算和异常资料；不启动交易终端。"""
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest
from pydantic import ValidationError

from cn_futures_bridge.config import ExecutionConfig
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.models import LimitOrder
from cn_futures_bridge.terminal.market import emulate_market
from cn_futures_bridge.terminal.native import InstrumentInfo
from cn_futures_bridge.terminal.pricing import normalize_limit, validate_limit


def request(value: str | float, side: str = "buy", offset: str = "open") -> LimitOrder:
    return LimitOrder.model_validate({'exchange_id':'DCE','instrument_id':'m2701','side':side,
        'offset':offset,'volume':1,'price':Decimal(str(value))})


def metadata(**changes) -> InstrumentInfo:
    return InstrumentInfo.model_validate({'found':True,'data_ready':True,'status':3,'tick':1,
        'lower':3206,'upper':3614,'limit_min_volume':1,'limit_max_volume':100,**changes})


def test_direction_noise_and_inclusive_clamp_boundaries() -> None:
    cases = [('buy','3486',3486,[]), ('buy','3486.0000000000005',3486,['float_noise']),
        ('sell','3485.9999999999995',3486,['float_noise']),
        ('buy','3514.35',3514,['tick_floor']), ('sell','3514.35',3515,['tick_ceil']),
        ('buy','3675',3614,['upper_limit']), ('sell','3190',3206,['lower_limit']),
        ('buy','3794.7',3614,['upper_limit']), ('sell','3045.7',3206,['lower_limit']),
        ('buy',3614*1.05,3614,['float_noise','upper_limit']),
        ('buy','3206',3206,[]), ('sell','3614',3614,[])]
    for side, value, expected, reasons in cases:
        for offset in ('open','close'):
            original = request(value,side,offset)
            actual, observed = normalize_limit(original,metadata(),.05)
            assert (actual.price,observed)==(Decimal(expected),reasons), (side,value,offset)
            assert original.price==Decimal(str(value)) and original is not actual
            assert actual.model_dump(exclude={'price'})==original.model_dump(exclude={'price'})
            validate_limit(actual,metadata())


def test_invalid_price_is_rejected_before_rounding_or_large_division() -> None:
    for side, value in [('buy','3190'),('sell','3675'),('buy','3794.71'),('sell','3045.69'),
                        ('buy','1e100'),('sell','1e-300')]:
        with pytest.raises(BridgeError) as error:
            normalize_limit(request(value,side),metadata(),.05)
        assert error.value.status==422 and error.value.code=='INVALID_ARGUMENTS'
        assert error.value.details and error.value.details[0].context is not None
        context = error.value.details[0].context
        assert context.requested_price==float(value) and context.price_tick==1
        assert (context.lower_limit,context.upper_limit,context.max_deviation_ratio)==(3206,3614,.05)
    # 容差之外的真实小数仍按方向处理，不能被归入浮点尾差。
    result,reasons = normalize_limit(request('3485.999999998'),metadata(),.05)
    assert result.price==3485 and reasons==['tick_floor']


def test_non_decimal_place_ticks_and_low_decimal_context() -> None:
    with localcontext() as context:
        context.prec=3
        for tick, raw, buy, sell in [('5','102.1','100','105'),('.5','100.2','100','100.5'),
                                   ('.2','100.1','100','100.2'),('.005','100.126','100.125','100.130')]:
            info=metadata(tick=float(tick),lower=90,upper=110)
            for side,expected in [('buy',buy),('sell',sell)]:
                actual,_=normalize_limit(request(raw,side),info,.05)
                assert actual.price==Decimal(expected)
                assert (Fraction(actual.price)/Fraction(tick)).denominator==1
        with pytest.raises(BridgeError) as error:
            normalize_limit(request('1e100'),metadata(),.05)
        assert error.value.code=='INVALID_ARGUMENTS'


def test_missing_and_inconsistent_price_rules_do_not_mean_unlimited() -> None:
    for changes in ({'tick':0},{'lower':0},{'upper':0},{'lower':3615},{'upper':float('inf')},
                    {'tick':float('nan')},{'found':False},{'data_ready':False},
                    {'lower':100.1,'upper':100.2,'tick':1}):
        with pytest.raises(BridgeError) as error:
            normalize_limit(request('3400'),metadata(**changes),.05)
        assert error.value.status==503 and error.value.code=='SERVICE_NOT_READY'
        assert 'NaN' not in error.value.response('test').model_dump_json()
        assert 'Infinity' not in error.value.response('test').model_dump_json()
    with pytest.raises(BridgeError) as error:
        normalize_limit(request('3206.05'),metadata(lower=3206.01),.05)
    assert error.value.code=='INVALID_ARGUMENTS'  # 不抬高买价来凑区间内第一跳。


def test_configured_ratio_and_strict_final_validation() -> None:
    assert ExecutionConfig().price_max_deviation_ratio==.05
    assert ExecutionConfig(price_max_deviation_ratio=0).price_max_deviation_ratio==0
    actual,reasons=normalize_limit(request('3650'),metadata(),.02)
    assert actual.price==3614 and reasons==['upper_limit']
    for value in (-.1,1,float('nan'),float('inf'),'0.05',True):
        with pytest.raises(ValidationError):
            ExecutionConfig.model_validate({'price_max_deviation_ratio':value})
    with pytest.raises(BridgeError) as error:
        normalize_limit(request('3615'),metadata(),0)
    assert error.value.code=='INVALID_ARGUMENTS'
    assert normalize_limit(request('3514.35'),metadata(),0)[0].price==3514
    with pytest.raises(BridgeError) as error:
        validate_limit(request('3514.35'),metadata())
    assert error.value.code=='INVALID_ARGUMENTS'
    with pytest.raises(BridgeError) as error:
        normalize_limit(request('9007199254740993'),metadata(lower=9007199254740980.,upper=9007199254741000.),.05)
    assert error.value.code=='INVALID_ARGUMENTS'


def test_market_still_uses_exact_exchange_boundary_ioc() -> None:
    for side, expected in [('buy',3614),('sell',3206)]:
        result=emulate_market(request('3400',side),metadata())
        assert result.price==expected and result.time_in_force=='IOC'
    with pytest.raises(BridgeError) as error:
        emulate_market(request('3400'),metadata(upper=3614.1))
    assert error.value.code=='SERVICE_NOT_READY'
