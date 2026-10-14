"""作者状态统一判定与展示工具。

供「作者管理」「已删除作品」及其他需要展示作者信息的页面复用。
核心约定：
- 状态只有四种：正常 / 已注销 / 待校验 / 未知；
- 「已注销」文案统一取 AUTHOR_DELETED_LABEL；
- 任何情况下不允许出现空白占位：缺失、异常一律兜底为「已注销」或「信息加载失败」；
- 已注销只是弱化展示与提示，不拦截双击跳转主页。
"""
from PySide6.QtGui import QColor

from ..core.db import DISABLE_REASON_MANUAL

# ---- 状态常量 ----
AUTHOR_STATUS_ACTIVE = "active"      # 正常
AUTHOR_STATUS_DELETED = "deleted"    # 已注销（账号已离开 pixiv / 不存在）
AUTHOR_STATUS_DISABLED = "disabled"  # 已停用（用户手动关闭，账号本身正常）
AUTHOR_STATUS_PENDING = "pending"    # 待校验（刚按 ID 添加，尚未拉到昵称）
AUTHOR_STATUS_UNKNOWN = "unknown"    # 信息加载失败 / 读取异常

# ---- 统一文案 ----
AUTHOR_DELETED_LABEL = "已注销"
AUTHOR_DISABLED_LABEL = "已停用"
AUTHOR_PENDING_LABEL = "待校验"
AUTHOR_UNKNOWN_LABEL = "信息加载失败"

_STATUS_TEXT = {
    AUTHOR_STATUS_DELETED: AUTHOR_DELETED_LABEL,
    AUTHOR_STATUS_DISABLED: AUTHOR_DISABLED_LABEL,
    AUTHOR_STATUS_PENDING: AUTHOR_PENDING_LABEL,
    AUTHOR_STATUS_UNKNOWN: AUTHOR_UNKNOWN_LABEL,
}

# 状态列 → 主题令牌色
STATUS_TOKEN = {
    AUTHOR_STATUS_ACTIVE: "success",
    AUTHOR_STATUS_DELETED: "text_muted",
    AUTHOR_STATUS_DISABLED: "warn",
    AUTHOR_STATUS_PENDING: "warn",
    AUTHOR_STATUS_UNKNOWN: "danger",
}


def _get(row, key, default=None):
    """兼容 dict 与 sqlite3.Row 两种记录。"""
    try:
        if row is None:
            return default
        if hasattr(row, "keys"):
            return row[key] if key in row.keys() else default
        return row[key]
    except Exception:
        return default


def resolve_author_status(row) -> str:
    """统一判定作者状态，满足任一条件即判定为「已注销」：

    1. 记录状态为注销 / 禁用：enabled 为 0 / False / '0'；
    2. 作者记录缺失（None）或 author_id 为空；
    3. 昵称等关键信息缺失，且并非「待校验」（曾入名单但信息丢失）。

    其余分支：
    - 昵称缺失但 pending_validate=1 → 待校验（刚添加，尚未拉取信息）；
    - 判定过程发生任何异常 → 未知（加载失败）。
    """
    try:
        if row is None:
            return AUTHOR_STATUS_DELETED
        aid = str(_get(row, "author_id") or "").strip()
        if not aid:
            return AUTHOR_STATUS_DELETED
        if not int(_get(row, "enabled", 1) or 0):
            # 区分「手动停用」与「账号已注销」；历史数据无原因时按注销处理（保持旧行为）
            reason = str(_get(row, "disabled_reason") or "").strip().lower()
            if reason == DISABLE_REASON_MANUAL:
                return AUTHOR_STATUS_DISABLED
            return AUTHOR_STATUS_DELETED
        name = str(_get(row, "author_name") or "").strip()
        if not name:
            if int(_get(row, "pending_validate", 0) or 0):
                return AUTHOR_STATUS_PENDING
            return AUTHOR_STATUS_DELETED
        return AUTHOR_STATUS_ACTIVE
    except Exception:
        return AUTHOR_STATUS_UNKNOWN


def author_display_name(row) -> tuple:
    """返回 (显示名, 状态)。保证返回值永远非空。"""
    status = resolve_author_status(row)
    if status in (AUTHOR_STATUS_DELETED, AUTHOR_STATUS_DISABLED):
        raw = str(_get(row, "author_name") or "").strip()
        label = AUTHOR_DELETED_LABEL if status == AUTHOR_STATUS_DELETED else AUTHOR_DISABLED_LABEL
        return (f"{raw}（{label}）" if raw else label, status)
    if status in _STATUS_TEXT:
        return (_STATUS_TEXT[status], status)
    name = str(_get(row, "author_name") or "").strip()
    if not name:
        return (AUTHOR_UNKNOWN_LABEL, AUTHOR_STATUS_UNKNOWN)
    return (name, AUTHOR_STATUS_ACTIVE)


def status_text(status: str) -> str:
    """「状态」列的文案。"""
    if status == AUTHOR_STATUS_ACTIVE:
        return "启用"
    return _STATUS_TEXT.get(status, AUTHOR_UNKNOWN_LABEL)


def decorate_name_item(item, status: str, accent_color: str, muted_color: str):
    """昵称单元格统一样式：
    - 正常：主题色 + 下划线，提示双击跳转；
    - 已注销 / 待校验：弱化灰、无下划线，已注销附说明 tooltip（不拦截跳转）。
    """
    try:
        if status == AUTHOR_STATUS_ACTIVE:
            item.setForeground(QColor(accent_color))
            font = item.font()
            font.setUnderline(True)
            item.setFont(font)
            item.setToolTip("双击打开 Pixiv 主页")
        else:
            item.setForeground(QColor(muted_color))
            font = item.font()
            font.setUnderline(False)
            item.setFont(font)
            if status == AUTHOR_STATUS_DELETED:
                item.setToolTip("该账号已注销；双击仍可打开其 Pixiv 主页（页面为空属预期）")
            elif status == AUTHOR_STATUS_DISABLED:
                item.setToolTip("你手动停用了该作者，账号本身正常；双击可打开其 Pixiv 主页")
            elif status == AUTHOR_STATUS_PENDING:
                item.setToolTip("已加入名单，待下次抓取时补全昵称")
            else:
                item.setToolTip("作者信息缺失或加载失败")
    except Exception:
        pass
    return item
