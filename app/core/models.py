class Status:
    PENDING = "pending"
    OK = "ok"
    OUTDATED = "outdated"
    DELETED = "deleted"
    SKIPPED = "skipped"
    FAILED_AUTH = "failed_auth"
    FAILED_403 = "failed_403"
    FAILED_NETWORK = "failed_network"
    FAILED_PARSE = "failed_parse"


STATUS_LABELS = {
    Status.PENDING: "待下载",
    Status.OK: "已同步",
    Status.OUTDATED: "待重打包",
    Status.DELETED: "远端已删除",
    Status.SKIPPED: "已跳过",
    Status.FAILED_AUTH: "登录失效",
    Status.FAILED_403: "UA/风控",
    Status.FAILED_NETWORK: "网络失败",
    Status.FAILED_PARSE: "解析/打包失败",
}

FAILED_STATUSES = (
    Status.FAILED_AUTH,
    Status.FAILED_403,
    Status.FAILED_NETWORK,
    Status.FAILED_PARSE,
)


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)
