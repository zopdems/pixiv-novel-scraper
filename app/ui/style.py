"""样式入口（兼容旧引用）。

历史代码里 `from .ui.style import QSS`。新的视觉实现已经迁移到 `theme.py`，
这里保留一个薄封装，避免旧引用报错。
"""

from .theme import (  # noqa: F401  （向后兼容导出）
    DARK,
    LIGHT,
    apply_theme,
    build_palette,
    build_qss,
    current_mode,
    resolve_mode,
    tokens,
)

QSS = build_qss("light")
