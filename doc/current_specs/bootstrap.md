# SimNow 终端启动与诊断

项目整体遵循 [project_principles.md](project_principles.md) 的高性能与可靠性原则；终端适配和后续业务接口按其中的优先级选择操作方式。

## 范围

当前提供空账号启动、版本校验、专用桌面、进程日志、状态和截图 API；Python 3.11+，无第三方 Python 运行依赖。账号与交易自动化尚未接入，填写账号不触发登录；environment 只支持 simnow。

固定包和原生模拟站点由根目录 terminal.lock.toml 定义。构建验证安装包 SHA256、主程序、上期技术 / 9999 站点，提取随包依赖并缩减站点列表；不改写该站点的服务器或认证参数。原生 SimNow 全天站点有 GUI 登录、行情、资金显示及拒单的历史样本；Wine 11 下电信2站点已通过 GUI 验证非零资金、挂单/撤单、买入开仓/卖出平仓成交及非空表格查询。能力、读取路径、耗时与未验证项见 [terminal_capabilities.md](terminal_capabilities.md)。这些客户端实测不表示自动登录或 REST 交易接口已经实现，也不证明其他站点兼容。

## 配置与运行

完整配置示例为根目录 config.example.toml，本地配置为 config.toml。两者使用相同 schema；实际配置与安装包缓存不进入版本控制，实际配置不进入镜像。init-config/up 仅在本地配置缺失时复制示例。

配置节为 bridge、account、api、desktop、vnc；缺失、未知或类型错误直接拒绝。账号与 token 在对象表示、状态和错误中隐藏。API/桌面端口由启动脚本发布至宿主机 127.0.0.1，VNC 访问与 API token 分开；本阶段 VNC 依赖本机绑定与必要的 SSH 转发。

Containerfile 的 headless 与 vnc 共用 runtime 层，包括固定版本终端、32 位 Wine、字体、Xvfb、Openbox、截图与服务；vnc 只添加远程桌面组件和启动标记。两个变体的数据卷契约一致，同一数据目录通过文件锁排他使用。

公共运行层固定 WineHQ 稳定版 Wine 11.0，`wine-stable:i386` 与 `wine-stable-i386:i386` 均为 `11.0.0.0~bookworm-1`；通过官方签名软件源安装，签名公钥校验值固定在 Containerfile。只安装这一套 Wine，使用 `/opt/wine-stable/bin`，保留 `WINEARCH=win32`，不随构建自动追随新版。升级前先停止实例并备份数据卷，旧 Wine 前缀不承诺可直接降级。

data_dir 默认 /data，其中 terminal 为终端状态和运行文件，wine 为专用前缀，logs 为各进程日志。第一次启动复制提取结果，后续启动按镜像清单检查全部随包文件；变化时返回 TERMINAL_CHANGED，不自动覆盖。终端直接启动固定 q7_release.exe，不经更新启动器。

Xvfb 禁用屏保，headless 的显示和截图不依赖 VNC 客户端连接。

图形进程使用镜像预建、root 所有且权限为 1777 的 /tmp/.X11-unix；Xvfb 使用 -noreset，健康探测断开不会重置屏幕，随后窗口管理器可继续连接。

先确认虚拟屏幕与窗口管理器就绪，再启动可选 VNC、引导 Wine 会话并启动终端；前缀持久化不跳过会话引导，字体映射仅首次执行。启动时按进程身份及“用户登录”标题确认窗口。初始化失败或终端进程退出不自动重试，保留已有桌面与 VNC 排查错误。停止容器清理自身进程和 Wine 会话；down 保留命名数据卷，切换变体需先停止旧实例。

## HTTP 契约

| 方法与路径 | 结果 |
| --- | --- |
| GET /healthz | 200，{"status":"ok"}；仅表示 HTTP 存活，不要求 token |
| GET /v1/status | 200，当前启动状态 |
| GET /readyz | 窗口可见且启动未失败为 200，否则 503；响应为状态对象 |
| GET /v1/desktop/screenshot | 200 image/png；不可截图为 503 错误对象 |

非空 api.token 要求其余 GET 接口的 Authorization: Bearer token；错误 token 返回 401。响应禁止缓存。未知 GET 返回 404，POST 返回 405，不触发桌面或交易操作。

状态字段：environment、stage、state、terminal_version、terminal_window_visible、account_configured、login_state、trading_ready、automation_enabled、vnc_enabled、error。stage 固定 terminal_bootstrap，login_state 固定 unverified，交易和自动化字段固定 false。

state 为 starting、window_visible、window_missing、failed。窗口标题探测只证明可见性，不证明登录成功或界面可正常交易。error 为 null 或 code/message；Wine 初始化、显示超时、窗口等待、文件变化和关键进程退出分别报告。

## 正式入口与验证

用户入口为 `uv run python tools/bridge.py`，子命令包括 init-config、fetch、build、up、down、status、screenshot、logs、test。build 选择 all/headless/vnc，up 选择 vnc/headless；具体示例见根目录 README.md。

默认 test 只验证本地配置与诊断 API，不访问外部服务或启动 Wine；真实镜像构建与空账号启动由独立的 build/up/status/screenshot/down 验证。资金、持仓、委托、日志事件解析和键盘/导入导出交易路径尚未接入正式入口，GUI 能力验证不扩大这些入口的职责。
