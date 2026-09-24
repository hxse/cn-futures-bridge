"""最小路由契约检查，不启动执行器、不访问参考服务或交易网络。"""

import asyncio

from httpx import ASGITransport, AsyncClient

from cn_futures_bridge.api import create_app
from cn_futures_bridge.config import Settings
from cn_futures_bridge.service import BridgeService


def test_eight_routes_and_validation_before_execution() -> None:
    app = create_app(BridgeService(Settings()), manage_lifecycle=False)
    async def verify() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://cfb.test") as client:
            schema = (await client.get("/openapi.json")).json()
            methods = {"create_market_order": "post", "create_limit_order": "post", "cancel_order": "post",
                       "fetch_orders": "get", "fetch_trades": "get", "fetch_positions": "get",
                       "fetch_balance": "get", "fetch_trading_status": "get"}
            assert {path: set(item) for path, item in schema["paths"].items() if path.startswith("/cfb/")} == {
                "/cfb/"+name: {method} for name, method in methods.items()}
            cancel_schema = schema["paths"]["/cfb/cancel_order"]["post"]["requestBody"]["content"]["application/json"]["schema"]
            assert cancel_schema["discriminator"]["propertyName"] == "by"
            assert schema["components"]["schemas"]["LimitOrder"]["properties"]["price"]["type"] == "number"
            execution = schema["components"]["schemas"]["OrderExecution"]["properties"]
            assert {'requested_price','price_adjusted','price_adjustments'} <= execution.keys()
            description = schema['paths']['/cfb/create_limit_order']['post']['description']
            assert '5%' in description and 'execution.requested_price' in description
            assert 'context' in schema['components']['schemas']['FieldProblem']['properties']
            order = {"exchange_id": "CZCE", "instrument_id": "RM701", "side": "buy", "offset": "open", "volume": 1, "price": 2323}
            valid = await client.post("/cfb/create_limit_order", json=order)
            assert valid.status_code == 503 and valid.json()["error"]["code"] == "SERVICE_NOT_READY"
            assert valid.headers["X-Request-ID"] == valid.json()["request_id"]
            assert valid.headers["Cache-Control"] == "no-store"
            for values in ({**order, "volume": True}, {**order, "price": "2323"}, {**order, "extra": 1}):
                invalid = await client.post("/cfb/create_limit_order", json=values)
                assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "INVALID_ARGUMENTS"
                assert invalid.json()["submission_status"] is None
            assert (await client.post("/cfb/create_market_order", json=order)).status_code == 422
            market = await client.post("/cfb/create_market_order", json={k: v for k, v in order.items() if k != "price"})
            assert market.status_code == 503 and market.json()["error"]["code"] == "SERVICE_NOT_READY"
            for suffix in ("mode=sandbox&mode=live", "unexpected=1"):
                result = await client.get("/cfb/fetch_positions?"+suffix)
                assert result.status_code == 422 and result.json()["error"]["code"] == "INVALID_ARGUMENTS"
            live = await client.get("/cfb/fetch_balance?mode=live")
            assert live.status_code == 409 and live.json()["error"]["code"] == "ENVIRONMENT_MISMATCH"
            ioc = await client.post("/cfb/create_limit_order", json={**order, "time_in_force": "IOC"})
            assert ioc.status_code == 503 and ioc.json()["error"]["code"] == "SERVICE_NOT_READY"
            unsupported = await client.post("/cfb/create_limit_order", json={**order, "time_in_force": "FOK"})
            assert unsupported.status_code == 501 and unsupported.json()["error"]["code"] == "CAPABILITY_NOT_SUPPORTED"
            mixed = await client.post("/cfb/cancel_order", json={"exchange_id": "CZCE", "instrument_id": "RM701",
                "by": "exchange_order", "order_sys_id": " 001", "front_id": 1})
            assert mixed.status_code == 422 and mixed.json()["error"]["code"] == "INVALID_ARGUMENTS"
            assert (await client.get("/cfb/fetch_trading_status?exchange_id=CZCE&product_id=RM")).status_code == 503
            reference = {"exchange_id": "DCE", "instrument_id": "m2701", "trading_day": "20260924",
                         "front_id": "3", "session_id": "-12", "order_ref": "18"}
            queried = await client.get("/cfb/fetch_orders", params=reference)
            assert queried.status_code == 503 and queried.json()["error"]["code"] == "SERVICE_NOT_READY"
            for invalid in ({"order_ref": "18"}, {**reference, "order_sys_id": "599159"}):
                result = await client.get("/cfb/fetch_orders", params=invalid)
                assert result.status_code == 422 and result.json()["error"]["code"] == "INVALID_ARGUMENTS"
    asyncio.run(verify())
