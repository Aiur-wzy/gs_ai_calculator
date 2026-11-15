"""Privacy-preserving Chinese descriptions of the pinned CLI error schema.

Never infer from or retain provider message/additionalDetails: those strings
may contain request bodies, credentials, local paths or proxy configuration.
This module only formats known discriminants and validated HTTP integers.
"""
from __future__ import annotations

_ERRORS = {
    "contextWindowExceeded": ("本轮输入超过模型上下文上限", "缩小输入或按需分页读取资料，再由用户决定是否继续。"),
    "sessionBudgetExceeded": ("CLI报告本次会话预算已用尽", "核查会话预算；不要自动开启新轮次绕过限制。"),
    "usageLimitExceeded": ("服务报告当前用量限制已达到", "额度与重置由用户管理；保留候选，等待用户决定后续操作。"),
    "serverOverloaded": ("模型服务当前繁忙", "保留当前候选，稍后由用户决定是否重试。"),
    "cyberPolicy": ("请求受到服务端策略限制", "核查任务内容及服务使用要求；不要通过切换身份绕过限制。"),
    "internalServerError": ("模型服务发生内部错误", "保留当前候选，稍后由用户决定是否重试。"),
    "unauthorized": ("模型服务未接受当前登录认证", "在Codex中核查登录状态，再重新连接；客户端不接收密钥或密码。"),
    "badRequest": ("模型服务拒绝了请求参数", "核查所选模型、推理档位和协议参数；不能仅凭此信息确定哪个参数错误。"),
    "threadRollbackFailed": ("CLI线程回退失败", "保留候选并检查会话状态，避免重复提交同一操作。"),
    "sandboxError": ("CLI报告执行环境错误", "检查客户端的环境隔离配置，不应自动扩大权限。"),
    "other": ("CLI报告了未分类错误", "结构化信息不足以判断原因；请保留本次安全诊断摘要。"),
    "httpConnectionFailed": ("无法完成与模型服务的HTTP连接", "检查网络与已配置代理；账户查询成功不等于推理连接可用。"),
    "responseStreamConnectionFailed": ("无法建立模型回复数据流", "检查推理连接、网络与已配置代理；尚不能据此确定是代理还是服务故障。"),
    "responseStreamDisconnected": ("模型回复数据流在完成前断开", "保留已有结果；检查网络稳定性后，由用户决定是否继续。"),
    "responseTooManyFailedAttempts": ("CLI达到回复传输失败次数上限", "检查网络和服务状态；不要自动开启下一轮重试。"),
    "activeTurnNotSteerable": ("当前CLI轮次不接受追加消息", "等待当前回退审查或压缩等操作结束，再明确发送。"),
    "unknown": ("CLI报告执行错误，但未提供可识别的结构化原因", "当前摘要不足以判断是网络、模型权限还是请求参数问题。"),
}
_STRUCTURED = frozenset({"httpConnectionFailed", "responseStreamConnectionFailed",
                         "responseStreamDisconnected", "responseTooManyFailedAttempts",
                         "activeTurnNotSteerable"})
_HTTP_ERRORS = _STRUCTURED - {"activeTurnNotSteerable"}
_HTTP_LABELS = {
    400: "请求被拒绝", 401: "认证未通过", 403: "服务拒绝访问", 404: "请求资源不存在",
    408: "请求超时", 413: "请求过大", 422: "请求内容无法处理", 429: "服务限流或用量受限",
    500: "服务内部错误", 502: "网关错误", 503: "服务暂不可用", 504: "网关超时",
}
_HTTP_HINTS = {
    400: "核查所选模型、推理档位及协议参数；不记录原始请求内容。",
    401: "在Codex中核查登录状态，再重新连接。",
    403: "核查当前账户和所选模型的访问权限；不要自动切换账户。",
    404: "核查CLI支持的模型与服务协议；此状态不能单独证明模型名称错误。",
    413: "减少单次发送的数据，改为按需分页读取。",
    422: "核查模型请求与工具声明的协议格式。",
    429: "额度与重置由用户管理；保留当前候选，等待用户决定是否继续。",
}

_NATIVE_ERRORS = {
    "provider_http_401": ("DeepSeek API认证未通过", "重新输入有效API Key；App不会把Key写入聊天记录。"),
    "provider_http_402": ("DeepSeek API余额不足", "余额与充值由用户管理；候选已经保留。"),
    "provider_http_429": ("DeepSeek API请求受到限流", "稍后由用户决定是否重新发送；App不会自动重试收费推理。"),
    "provider_http_500": ("DeepSeek服务发生内部错误", "候选已经保留，稍后由用户决定是否重试。"),
    "provider_http_503": ("DeepSeek服务当前繁忙", "候选已经保留，稍后由用户决定是否重试。"),
    "provider_network": ("无法完成与DeepSeek API的连接", "检查网络和系统代理后，由用户决定是否重试。"),
    "provider_protocol": ("DeepSeek API返回了不兼容的数据", "保留候选并核对App与API版本。"),
    "provider_empty": ("DeepSeek未返回可显示回复", "候选已经保留；可调整任务后由用户决定是否继续。"),
    "tool_budget": ("本轮工具调用达到App上限", "检查已有候选与工具记录后，明确开启下一轮继续。"),
    "loop_budget": ("本轮Agent循环达到App上限", "检查已有候选与工具记录后，明确开启下一轮继续。"),
    "native_agent_failure": ("App原生Agent运行失败", "候选已经保留；检查配置和诊断后再继续。"),
}


def describe_provider_error(error: object, *, will_retry: object = None) -> dict:
    """Accept a TurnError (params.error or turn.error), return safe UI metadata.

    ``cli_will_retry`` records only the CLI's claim. It neither authorizes nor
    implements retries. Unknown variants and extra provider fields are omitted.
    """
    native_code = error.get("type") if isinstance(error, dict) else None
    if native_code in _NATIVE_ERRORS:
        message, hint = _NATIVE_ERRORS[native_code]
        return {"reason": "agent_provider_error", "message": message + "。", "hint": hint,
                "error_type": native_code, "http_status": None,
                "cli_will_retry": will_retry if type(will_retry) is bool else None}
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    error_type = "unknown"
    http_status = None
    if isinstance(info, str) and info in _ERRORS and info not in _STRUCTURED:
        error_type = info
    elif isinstance(info, dict) and len(info) == 1:
        variant = next(iter(info))
        payload = info[variant]
        if variant in _STRUCTURED and isinstance(payload, dict):
            error_type = variant
            status = payload.get("httpStatusCode")
            if variant in _HTTP_ERRORS and type(status) is int and 100 <= status <= 599:
                http_status = status
    message, hint = _ERRORS[error_type]
    if http_status is not None:
        label = _HTTP_LABELS.get(http_status)
        message += f"（HTTP {http_status}" + (f"：{label}" if label else "") + "）"
        hint = _HTTP_HINTS.get(http_status, hint)
    return {"reason": "agent_provider_error", "message": message + "。", "hint": hint,
            "error_type": error_type, "http_status": http_status,
            "cli_will_retry": will_retry if type(will_retry) is bool else None}
