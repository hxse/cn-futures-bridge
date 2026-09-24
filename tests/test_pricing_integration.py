"""真实执行器和确认器使用实际价，外部终端动作以本地事实替代。"""
from pathlib import Path

import pytest

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.journal import Journal
from cn_futures_bridge.logging_store import LogStore
from cn_futures_bridge.models import Operation
from cn_futures_bridge.results import OrderIdentity, SubmissionResult
from cn_futures_bridge.terminal.executor import Executor
from cn_futures_bridge.terminal.native import InstrumentInfo, Session
from cn_futures_bridge.terminal.tracking import TrackedSnapshot


def hexed(value: str) -> str:
    return value.encode('gb18030').hex()


@pytest.fixture
def execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings=Settings(bridge=BridgeConfig(data_dir=tmp_path))
    journal=Journal(settings)
    executor=Executor(settings,LogStore(settings),journal)
    session=Session(connected=True,trade_connected=True,market_connected=True,identity_match=True,
                    trading_day='20260925',front_id=3,session_id=12,status_bound=True,login_generation=0)
    identity=OrderIdentity(exchange_id='DCE',instrument_id='m2701',trading_day=session.trading_day,
                           front_id=session.front_id,session_id=session.session_id,order_ref='18')
    info=InstrumentInfo(found=True,data_ready=True,status=3,tick=1,lower=3206,upper=3614,
                        limit_min_volume=1,limit_max_volume=100)
    calls=[]
    state={'ioc':False}
    monkeypatch.setattr(executor,'validate_session',lambda **kwargs: session)
    monkeypatch.setattr(executor,'instrument',lambda *args: info)
    monkeypatch.setattr(executor.gui,'ensure_ready',lambda **kwargs: None)
    monkeypatch.setattr(executor.gui,'finish',lambda: calls.append('finish'))
    def submit(request, metadata, steps):
        calls.append(('submit',request.price,request.time_in_force))
        state['ioc']=request.time_in_force=='IOC'
        steps.identity=identity
        steps.save('submitting',effect='unknown')
        steps.save('submitted',effect='submitted')
        return SubmissionResult(request_id=steps.request_id,identity=identity)
    monkeypatch.setattr(executor.orders,'limit',submit)
    monkeypatch.setattr(executor.market,'limit_ioc',submit)
    def read(table, steps):
        calls.append(('read',table))
        if table!='orders': return []
        return [{'交易所':'DCE','合约':'m2701','买卖':'买','开平':'开仓','报单编号':'123',
                 '报单手数':'1','成交手数':'0','未成交手数':'1','报单价格':'3514',
                 '挂单状态':'已撤单' if state['ioc'] else '未成交',
                 '详细状态':'已撤单' if state['ioc'] else '报单已提交','报单时间':'10:00:00'}]
    def lookup(reference):
        assert reference==identity
        return TrackedSnapshot.model_validate({'orders':[{'front_id':3,'session_id':12,'exchange':2,
            'side':0,'offset':0,'hedge':1,'volume':1,'filled':0,'remaining':1,'price':3514,
            'instrument_hex':hexed('m2701'),'order_ref_hex':hexed('18'),'order_id_hex':hexed('123'),
            'message_hex':hexed('已撤单' if state['ioc'] else '报单已提交'),'time_hex':hexed('10:00:00')}],
            'trades':[]})
    monkeypatch.setattr(executor.verifier,'read',read)
    monkeypatch.setattr(executor.verifier,'lookup',lookup)
    return executor,journal,calls,submit,info


def operation(price: float = 3514.35, tif: str = 'GFD') -> Operation:
    return Operation(action='create_limit_order',parameters={'mode':'sandbox','exchange_id':'DCE',
        'instrument_id':'m2701','side':'buy','offset':'open','volume':1,'price':price,'time_in_force':tif})


@pytest.mark.parametrize('tif',['GFD','IOC'])
def test_actual_price_reaches_execution_and_exact_confirmation(execution,tif):
    executor,journal,calls,_,_=execution
    original=operation(tif=tif)
    journal.admit('price-case',original,'price-key')
    reply=executor.execute('price-case',original)
    assert reply.status==202 and not reply.blocked
    assert reply.body['execution']=={'kind':'limit','price':3514.0,'time_in_force':tif,
        'requested_price':3514.35,'price_adjusted':True,'price_adjustments':['tick_floor']}
    assert reply.body['order_id']=='123' and reply.body['verification']['status']=='observed'
    assert reply.body['verification']['orders'][0]['price']==3514
    assert ('submit',3514,tif) in calls and calls[-1]=='finish'
    assert original.parameters['price']==3514.35 and journal.lookup(original,'price-key')==reply
    changed=journal.lookup(operation(3514.9,tif),'price-key')
    assert changed and changed.status==409 and changed.body['error']['code']=='IDEMPOTENCY_CONFLICT'


@pytest.mark.parametrize('missing',[False,True])
def test_rejection_precedes_position_csv_and_trading_actions(execution,monkeypatch,missing):
    executor,journal,calls,_,info=execution
    if missing:
        monkeypatch.setattr(executor,'instrument',lambda *args: info.model_copy(update={'upper':0}))
    original=operation(3514.35 if missing else 1e100)
    journal.admit('price-case',original,None)
    reply=executor.execute('price-case',original)
    assert reply.status==(503 if missing else 422)
    assert reply.body['error']['code']==('SERVICE_NOT_READY' if missing else 'INVALID_ARGUMENTS')
    assert reply.body['submission_status'] is None and reply.body['order_id'] is None
    assert reply.body['execution'] is None and calls==['finish']
    assert reply.body['error']['details'][0]['context']['requested_price']==original.parameters['price']
    assert journal.unresolved()==0


def test_error_after_submission_keeps_price_decision(execution,monkeypatch):
    executor,journal,calls,submit,_=execution
    def failed(request,info,steps):
        submit(request,info,steps)
        raise BridgeError('TERMINAL_DATA_INVALID','fixture: 提交后读取失败',502)
    monkeypatch.setattr(executor.orders,'limit',failed)
    original=operation()
    journal.admit('price-case',original,'price-key')
    reply=executor.execute('price-case',original)
    assert reply.status==502 and reply.body['submission_status']=='submitted'
    assert reply.body['execution']['requested_price']==3514.35
    assert reply.body['execution']['price']==3514 and reply.body['execution']['price_adjustments']==['tick_floor']
    assert reply.body['identity']['order_ref']=='18' and calls[-1]=='finish'
    assert journal.lookup(original,'price-key')==reply
