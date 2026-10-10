"""轻量矢量图标库。

内置 Feather 风格 24x24 描边图标，运行时用 QSvgRenderer 渲染成 QIcon，
无需任何外部资源文件，PyInstaller 打包也不会丢资源。

对外接口：
    icon(name, color, size)  -> QIcon        界面按钮 / 导航图标
    asset(name, color)       -> str          QSS url() 可用的 svg 文件路径
    app_mark(size)           -> QPixmap      应用品牌标识（渐变圆角 + P）
"""

import os
import tempfile

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap,
)
from PySide6.QtSvg import QSvgRenderer

# ---------------------------------------------------------------- 图标路径

# 描边类图标（stroke 渲染）
_STROKE = {
    # 导航
    "users": (
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
        '<circle cx="9" cy="7" r="4"/>'
        '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>'
        '<path d="M16 3.13a4 4 0 0 1 0 7.75"/>'
    ),
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>',
    "sliders": (
        '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/>'
        '<path d="M1 14h6M9 8h6M17 16h6"/>'
    ),
    "book": (
        '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>'
        '<path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>'
    ),
    # 操作
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": (
        '<path d="M3 6h18M8 6V4h8v2"/>'
        '<path d="M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>'
    ),
    "refresh": (
        '<path d="M23 4v6h-6M1 20v-6h6"/>'
        '<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>'
    ),
    "external": (
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
        '<path d="M15 3h6v6"/><path d="M10 14L21 3"/>'
    ),
    "upload": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/>'
    ),
    "download": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>'
    ),
    "folder": (
        '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'
    ),
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "close": '<path d="M18 6L6 18M6 6l12 12"/>',
    "power": '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><path d="M12 2v10"/>',
    # 状态 / 语义
    "key": (
        '<circle cx="7.5" cy="15.5" r="4.5"/>'
        '<path d="M10.8 12.2L21 2M17 6l3 3M14 9l3 3"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/>'
        '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10'
        ' 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'
    ),
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "database": (
        '<ellipse cx="12" cy="5" rx="9" ry="3"/>'
        '<path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>'
        '<path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>'
    ),
    "image": (
        '<rect x="3" y="3" width="18" height="18" rx="2"/>'
        '<circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>'
    ),
    "file-text": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/>'
    ),
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "alert": (
        '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3'
        'L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
        '<path d="M12 9v4M12 17h.01"/>'
    ),
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "sun": (
        '<circle cx="12" cy="12" r="4.5"/>'
        '<path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42'
        'M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>'
    ),
    "palette": (
        '<circle cx="13.5" cy="6.5" r=".6" fill="current" stroke="none"/>'
        '<circle cx="17.5" cy="10.5" r=".6" fill="current" stroke="none"/>'
        '<circle cx="8.5" cy="7.5" r=".6" fill="current" stroke="none"/>'
        '<circle cx="6.5" cy="12.5" r=".6" fill="current" stroke="none"/>'
        '<path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.93 0 1.65-.75 1.65-1.69'
        ' 0-.44-.18-.84-.44-1.13-.29-.29-.44-.65-.44-1.13a1.64 1.64 0 0 1'
        ' 1.67-1.67h2c3.05 0 5.56-2.5 5.56-5.55C21.97 6.01 17.46 2 12 2z"/>'
    ),
    "gauge": (
        '<path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/>'
        '<path d="M13.4 10.6L19 5"/><path d="M20.5 15.5A9 9 0 1 1 8.5 4.6"/>'
    ),
}

# 填充类图标（实心，用于主按钮上的播放 / 停止）
_FILL = {
    "play": '<path d="M7 4.5v15l13-7.5z"/>',
    "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2.5"/>',
}

# QSS 子控件用的独立形状（复选框勾、微调箭头）
_ASSET = {
    "check": ('<path d="M20 6L9 17l-5-5"/>', 3.0),
    "up": ('<path d="M18 15l-6-6-6 6"/>', 2.6),
    "down": ('<path d="M6 9l6 6 6-6"/>', 2.6),
}


def _svg(inner: str, color: str, stroke: float, fill: str = "none") -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="%s" stroke="%s" stroke-width="%s" '
        'stroke-linecap="round" stroke-linejoin="round">%s</svg>'
        % (fill, color, stroke, inner)
    ).encode("utf-8")


def _render(svg: bytes, px: int) -> QPixmap:
    """把 SVG 渲染进物理像素为 px 的画布，并声明 DPR=2 保证高清。"""
    renderer = QSvgRenderer(svg)
    pm = QPixmap(px, px)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(2.0)
    return pm


def icon(name: str, color: str = "#ffffff", size: int = 18) -> QIcon:
    """生成指定颜色与逻辑尺寸的 QIcon。"""
    if name in _FILL:
        svg = _svg(_FILL[name], color, 0, fill=color)
    else:
        inner = _STROKE.get(name)
        if inner is None:
            return QIcon()
        # 调色板图标里的圆点需要跟着前景色走
        inner = inner.replace("fill=\"current\"", "fill=\"%s\"" % color)
        svg = _svg(inner, color, 1.9)
    return QIcon(_render(svg, size * 2))


# ------------------------------------------------------- QSS 资源文件

_ASSET_DIR = None


def _asset_dir() -> str:
    global _ASSET_DIR
    if _ASSET_DIR is None:
        try:
            from ..config import app_data_dir
            base = os.path.join(app_data_dir(), "ui_assets")
        except Exception:
            base = os.path.join(tempfile.gettempdir(), "pixiv_novel_ui_assets")
        try:
            os.makedirs(base, exist_ok=True)
        except OSError:
            base = os.path.join(tempfile.gettempdir(), "pixiv_novel_ui_assets")
            os.makedirs(base, exist_ok=True)
        _ASSET_DIR = base
    return _ASSET_DIR


def asset(name: str, color: str = "#ffffff") -> str:
    """返回可供 QSS url() 引用的 svg 文件绝对路径（正斜杠）。"""
    if name not in _ASSET:
        return ""
    inner, width = _ASSET[name]
    safe = color.strip().lstrip("#").lower() or "none"
    path = os.path.join(_asset_dir(), "%s-%s.svg" % (name, safe))
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_svg(inner, color, width).decode("utf-8"))
    except OSError:
        return ""
    return path.replace("\\", "/")


# ------------------------------------------------------------ 品牌标识

_MARK_FROM = "#3FB7FF"
_MARK_TO = "#0064D8"


def app_mark(size: int = 40) -> QPixmap:
    """应用标识：蓝青渐变圆角方块 + 白色 P。"""
    dpr = 3
    px = size * dpr
    pm = QPixmap(px, px)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)

    grad = QLinearGradient(0, 0, px, px)
    grad.setColorAt(0.0, QColor(_MARK_FROM))
    grad.setColorAt(1.0, QColor(_MARK_TO))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(grad))
    radius = px * 0.28
    painter.drawRoundedRect(QRectF(0, 0, px, px), radius, radius)

    font = QFont("Segoe UI Semibold", 1, QFont.Bold)
    font.setPixelSize(int(px * 0.54))
    painter.setFont(font)
    painter.setPen(QPen(QColor("#ffffff")))
    painter.drawText(QRectF(0, px * 0.02, px, px * 0.98), Qt.AlignCenter, "P")
    painter.end()

    pm.setDevicePixelRatio(dpr)
    return pm


def app_icon(size: int = 64) -> QIcon:
    """窗口 / 托盘图标。"""
    return QIcon(app_mark(size))
