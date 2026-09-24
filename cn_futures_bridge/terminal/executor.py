"""独立执行器中的唯一终端业务链路。"""

from datetime import datetime, timezone
import logging
import subprocess
import time

from ..artifacts import ArtifactStore
from ..config import Settings
from ..errors import BridgeError, ErrorDetail, FieldProblem
from ..journal import Journal, error_reply
from ..logging_store import LogStore
from ..models import (BalanceQuery, CancelByExchange, LimitOrder, MarketOrder, Operation, OrderQuery,
                      PositionQuery, TradeQuery, TradingStatusQuery)
from ..results import (BalanceResult, OrderExecution, OrderIdentity, OrdersResult, PositionsResult, Reply, ResultModel,
                       Snapshot, SubmissionResult, TradesResult, TradingStatusResult)
from ..reconnect import CONNECTION_ERRORS
from .csv_data import funds_text, read_csv
from .gui import Gui
from .market import MarketActions
from .native import InstrumentInfo, NativeClient, Session
from .orders import OrderActions
from .policy import EXCHANGE_NUMBERS, validate_capability
from .steps import Steps
from .verification import CsvVerifier
from .tracking import track

LOG = logging.getLogger(__name__)


class Executor:
    def __init__(self, settings: Settings, logs: LogStore, journal: Journal):
        self.settings = settings
        self.logs = logs
        self.journal = journal
        self.artifacts = ArtifactStore(settings)
        self.native = NativeClient(settings, logs)
        self.gui = Gui(self.native, settings)
        self.orders = OrderActions(self.gui, self.read)
        self.market = MarketActions(self.orders)
        self.verifier = CsvVerifier(self.read, settings.execution, lambda identity: track(self.native, identity))
        self.session: Session | None = None
        self.disconnected = False
        self.login_generation: int | None = None
        self.identity_lost = False
        self.login_established = False
        self.blocked = False
        self.error: ErrorDetail | None = None
        self.login_state = "not_configured" if not settings.account.configured else "logging_in"

    def start(self) -> None:
        self.native.start()
        self.native.windows()
        if not self.settings.account.configured:
            self.gui.prepare_login()
            self.native.complete_startup()
            return
        self.gui.login()
        deadline = self.native.startup_deadline
        assert deadline is not None
        while True:
            initial = self.native.session()
            if self.login_generation is None:
                self.login_generation = initial.login_generation
            elif initial.login_generation != self.login_generation:
                self.identity_lost = True
                raise BridgeError("SERVICE_NOT_READY", "启动期间再次进入登录界面，停止建立账户绑定")
            if initial.identity_match and initial.connected and initial.status_bound:
                break
            if time.monotonic() >= deadline:
                if initial.status_bound and not initial.connected:
                    raise BridgeError("CONNECTION_FAILED", "启动时限内交易或行情连接未就绪")
                raise BridgeError("SERVICE_NOT_READY", "启动时限内账户身份或交易/行情连接未就绪")
            time.sleep(max(.1, self.settings.execution.poll_interval_ms / 1000))
        self.login_established = True
        self.validate_session(rebind=True)
        self.gui.ensure_ready()
        self.native.complete_startup()
        self.gui.ensure_ready()
        LOG.info("启动账户身份及交易/行情连接已核对", extra={"step": "login_ready", "event": "end"})

    def validate_session(self, *, rebind: bool = False) -> Session:
        if not self.settings.account.configured:
            raise BridgeError("SERVICE_NOT_READY", "尚未配置账户")
        if not self.login_established:
            raise BridgeError("SERVICE_NOT_READY", "未完成按配置登录的账户核对，需要重启容器")
        if self.identity_lost:
            raise BridgeError("SERVICE_NOT_READY", "终端登录身份已失效，需要按配置重启容器")
        session = self.native.session()
        if self.login_generation is not None and session.login_generation != self.login_generation:
            self.identity_lost = True
            self.gui.reset_bindings()
            raise BridgeError("SERVICE_NOT_READY", "终端再次进入登录界面，停止使用旧账户缓存；请重启容器")
        if not session.status_bound or not session.connected or not session.identity_match:
            self.disconnected = True
            self.gui.reset_bindings()
            self.login_state = "disconnected"
            if session.status_bound and session.identity_match and not session.connected:
                raise BridgeError("CONNECTION_LOST", "交易或行情连接已断开")
            raise BridgeError("SERVICE_NOT_READY", "真实账户身份或原生对象未就绪，需要人工核对")
        if self.session and (self.disconnected or session.identity != self.session.identity):
            self.gui.reset_bindings()
            if not rebind or session.identity == self.session.identity:
                raise BridgeError("SERVICE_NOT_READY", "连接或会话已改变，需要确认新会话；同身份旧缓存须重启终端")
        self.session = session
        self.login_generation = session.login_generation
        self.disconnected = False
        self.login_state = "logged_in"
        return session

    def resume(self) -> None:
        if self.native.poisoned:
            raise BridgeError("GUI_UNRESPONSIVE", "旧原生调用未确认退出，须受控重启整个容器")
        self.gui.reset_bindings()
        self.validate_session(rebind=True)
        self.gui.finish()
        self.blocked = False
        self.error = None

    def fail(self, error: BridgeError) -> None:
        self.error = ErrorDetail(code=error.code, message=str(error))
        self.blocked = True
        if self.login_state == "logging_in":
            self.login_state = "failed"

    def failure_evidence(self, error: BridgeError) -> None:
        if error.code not in ("GUI_RESET_FAILED", "GUI_UNRESPONSIVE", "QUERY_TIMEOUT"):
            return
        try:
            directory = self.artifacts.create()
            steps = Steps(directory.name, "diagnostic", self.journal, directory)
            self.screenshot(steps)
            self.artifacts.finish(directory, keep=True)
        except (OSError, BridgeError):
            LOG.warning("诊断异常现场未能保存", extra={"step": "evidence"})

    def instrument(self, instrument: str, exchange: str, *, product: bool = False) -> InstrumentInfo:
        info = self.native.instrument(instrument, product=product)
        if not info.data_ready:
            raise BridgeError("SERVICE_NOT_READY", "终端标的资料尚未加载")
        if not info.found or info.name != instrument or info.exchange != EXCHANGE_NUMBERS[exchange]:
            location = ["query", "product_id"] if product else ["body", "instrument_id"]
            raise BridgeError("INVALID_ARGUMENTS", "终端资料未精确匹配请求的交易所和标的", 422,
                              details=[FieldProblem(loc=location, type="value_error", message="标的或所属交易所不匹配")])
        return info

    def read(self, table: str, steps: Steps) -> list[dict[str, str]]:
        with steps.step("export_" + table):
            self.validate_session()
            self.artifacts.clean()
            window = self.gui.grid(table)
            path = steps.path(table)
            self.native.export(window.hwnd, path)
            self.artifacts.clean()
            rows = read_csv(path, table, self.settings.artifacts.max_total_bytes)
            self.validate_session()
            return rows

    def query(self, operation: Operation, steps: Steps) -> ResultModel:
        request = operation.request()
        assert self.session is not None
        common = {"request_id": steps.request_id, "observed_at": datetime.now(timezone.utc).isoformat(),
                  "trading_day": self.session.trading_day or None, "source": "terminal_csv"}
        if isinstance(request, TradingStatusQuery):
            with steps.step("native_product_status"):
                info = self.instrument(request.product_id, request.exchange_id, product=True)
                LOG.info("品种 %s/%s 原生状态 %s，会话 %s", request.exchange_id, info.name,
                         info.status, self.session.identity, extra={"request_id": steps.request_id})
                if info.status not in range(1, 8):
                    raise BridgeError("TERMINAL_DATA_INVALID", "原生交易状态为未知编码", 502)
                return TradingStatusResult(request_id=steps.request_id, exchange_id=request.exchange_id,
                    product_id=request.product_id, observed_at=datetime.now(timezone.utc).isoformat(),
                    is_trading=info.status == 3)
        if isinstance(request, BalanceQuery):
            with steps.step("funds_dialog"):
                balance = funds_text(self.gui.balance_text())
            common.update(source="terminal_text", observed_at=datetime.now(timezone.utc).isoformat())
            return BalanceResult.model_validate({**common, "balance": balance})
        if isinstance(request, OrderQuery):
            if request.order_ref is not None:
                identity = OrderIdentity.model_validate(request.model_dump(include={
                    "exchange_id", "instrument_id", "trading_day", "front_id", "session_id", "order_ref"}))
                if identity.trading_day != self.session.trading_day:
                    raise BridgeError("CAPABILITY_NOT_SUPPORTED", "按引用查询只支持终端当前交易日", 501)
                rows, consistency = self.verifier.query_reference(request, identity, steps)
                common.update(source="terminal_csv_and_native")
                return OrdersResult.model_validate({**common, "orders": rows, "consistency": consistency, "identity": identity})
            rows, consistency = self.verifier.query("orders", request, steps)
            return OrdersResult.model_validate({**common, "orders": rows, "consistency": consistency})
        if isinstance(request, TradeQuery):
            rows, consistency = self.verifier.query("trades", request, steps)
            return TradesResult.model_validate({**common, "trades": rows, "consistency": consistency})
        if isinstance(request, PositionQuery):
            rows, consistency = self.verifier.query("positions", request, steps)
            return PositionsResult.model_validate({**common, "positions": rows, "consistency": consistency})
        raise BridgeError("INVALID_ARGUMENTS", "内部查询类型错误", 422)

    def execute(self, request_id: str, operation: Operation) -> Reply:
        self.gui.reset_bindings()
        self.native.timings.clear()
        native_only = operation.action == "fetch_trading_status"
        steps = Steps(request_id, operation.action, self.journal, None)
        error: BridgeError | None = None
        value: ResultModel | None = None
        owned = False
        try:
            request = operation.request()
            validate_capability(request, self.settings)
            if self.blocked:
                raise BridgeError("SERVICE_NOT_READY", "执行器已暂停，需要人工核对后恢复")
            with steps.step("prepare"):
                session = self.validate_session()
                steps.session = session
                self.gui.ensure_ready(keyboard=steps.writes or isinstance(request, BalanceQuery))
                if not native_only:
                    steps.directory = self.artifacts.create()
                owned = True
                if steps.writes and not session.trading_day:
                    raise BridgeError("SERVICE_NOT_READY", "缺少可信交易日，暂不允许写操作")
                steps.save("started", session=repr(session.identity))
            before = None
            if isinstance(request, (MarketOrder, CancelByExchange)):
                with steps.step("snapshot_before"):
                    before = self.verifier.snapshot(request, steps, before=True)
            if isinstance(request, LimitOrder):
                info = self.instrument(request.instrument_id, request.exchange_id)
                steps.execution = OrderExecution(kind="limit", price=float(request.price),
                                                 time_in_force="IOC" if request.time_in_force == "IOC" else "GFD")
                value = (self.market.limit_ioc(request, info, steps) if request.time_in_force == "IOC"
                         else self.orders.limit(request, info, steps))
            elif isinstance(request, MarketOrder):
                info = self.instrument(request.instrument_id, request.exchange_id)
                value = self.market.create(request, info, steps)
            elif isinstance(request, CancelByExchange):
                self.instrument(request.instrument_id, request.exchange_id)
                steps.execution = OrderExecution(kind="cancel")
                value = self.orders.cancel(request, steps)
            else:
                value = self.query(operation, steps)
            if isinstance(request, (MarketOrder, CancelByExchange)):
                assert before is not None and isinstance(value, SubmissionResult)
                with steps.step("verify_execution"):
                    value.execution = steps.execution
                    value.verification = self.verifier.observe(request, before, steps)
                    value.identity, value.order_id = steps.identity, steps.order_id or value.order_id
                    if isinstance(request, CancelByExchange) and steps.effect == "unknown":
                        if value.verification.status != "observed":
                            raise BridgeError("OPERATION_STATUS_UNKNOWN", "未出现撤单确认且 CSV 尚不能确认目标订单终态", 502)
                        steps.save("cancel_observed", effect="submitted")
            with steps.step("verify_session"):
                self.validate_session()
        except BridgeError as exc:
            if native_only and self.native.poisoned and exc.code == "GUI_UNRESPONSIVE":
                exc = BridgeError("QUERY_TIMEOUT", "原生状态读取未完整完成，执行器已隔离", 504)
            error = exc
        except Exception as exc:
            LOG.error("未预期执行异常：%s", type(exc).__name__, extra={"request_id": request_id})
            error = BridgeError("TERMINAL_DATA_INVALID", "终端操作异常，查看对应步骤日志", 502)
        finally:
            if owned:
                try:
                    with steps.step("cleanup"):
                        if not self.native.poisoned:
                            if (steps.capture_armed or steps.market_dialog is not None
                                    or steps.owned_row is not None or steps.form_state is not None):
                                self.gui.drain_notices()
                            self.orders.finish_capture(steps)
                            self.market.close_dialog(steps)
                            self.orders.cleanup(steps)
                            self.market.restore(steps)
                            self.gui.finish()
                        else:
                            raise BridgeError("GUI_RESET_FAILED", "旧调用未退出，保留文件并暂停派发")
                except Exception:
                    error = BridgeError("GUI_RESET_FAILED", "本次操作收尾未确认，保留证据并暂停派发")
                    self.blocked = True
            self.gui.reset_bindings()
            LOG.info("原生命令累计耗时（含通信）：%s",
                     {name: {"calls": count, "ms": round(seconds*1000, 3)}
                      for name, (count, seconds) in self.native.timings.items()},
                     extra={"request_id": request_id, "action": operation.action, "step": "native_timings"})
            if error and (error.code in CONNECTION_ERRORS or error.code in ("GUI_UNRESPONSIVE", "GUI_RESET_FAILED", "STORAGE_UNAVAILABLE", "SERVICE_NOT_READY")
                          or self.native.poisoned or (steps.effect == "unknown" and steps.owned_row is None)):
                self.fail(error)
            if error and error.code in ("GUI_UNRESPONSIVE", "GUI_RESET_FAILED"):
                if steps.directory:
                    self.screenshot(steps)
                else:
                    self.failure_evidence(error)
        if error:
            error.submission_status = steps.effect or error.submission_status
            error.identity = steps.identity
            error.order_id = steps.order_id or error.order_id
            if isinstance(value, ResultModel) and hasattr(value, "order_id"):
                error.order_id = error.order_id or getattr(value, "order_id")
            reply = error_reply(request_id, error, blocked=self.blocked)
            if isinstance(value, SubmissionResult):
                reply.body.update(execution=value.execution.model_dump(mode="json") if value.execution else None,
                                  verification=value.verification.model_dump(mode="json") if value.verification else None)
        else:
            assert value is not None
            if isinstance(value, Snapshot):
                value.observed_at = datetime.now(timezone.utc).isoformat()
            reply = Reply(request_id=request_id, status=202 if steps.writes else 200, body=value.model_dump(mode="json"))
        try:
            if steps.directory:
                self.artifacts.finish(steps.directory, keep=error is not None,
                                      protect=steps.effect == "unknown" or self.native.poisoned)
            if steps.writes:
                self.journal.finish(reply)
        except (OSError, BridgeError):
            error = BridgeError("STORAGE_UNAVAILABLE", "结果或工件状态未能可靠保存，停止派发",
                                submission_status=steps.effect)
            self.fail(error)
            reply = error_reply(request_id, error, blocked=True)
        return reply

    def screenshot(self, steps: Steps) -> None:
        assert steps.directory is not None
        try:
            self.artifacts.clean()
            subprocess.run(["cfb-capture", str(steps.directory / "failure.png")],
                           env=self.native.env, capture_output=True, timeout=3, check=True)
            self.artifacts.clean()
        except (OSError, subprocess.SubprocessError, BridgeError):
            LOG.warning("必要截图未取得", extra={"request_id": steps.request_id, "step": "evidence"})
