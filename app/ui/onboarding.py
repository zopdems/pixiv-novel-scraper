import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWizard, QWizardPage,
)

from .. import credentials
from ..pixiv.client import PixivClient
from .icons import app_mark, icon
from .theme import current_mode, tokens


class _StepPage(QWizardPage):
    """带页内标题头的向导页基类。"""

    def _build_header(self, layout: QVBoxLayout, step: int, icon_name: str,
                      title: str, subtitle: str):
        t = tokens(current_mode())

        head = QHBoxLayout()
        head.setSpacing(12)

        badge = QLabel(str(step))
        badge.setFixedSize(34, 34)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "background: %s; color: %s; border-radius: 2px;"
            "font-size: 14px; font-weight: 700;" % (t["accent_soft"], t["accent_text"])
        )
        head.addWidget(badge, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        head.addLayout(text_col, 1)

        lbl_title = QLabel(title)
        lbl_title.setProperty("class", "page-title")
        lbl_title.setStyleSheet("font-size: 17px;")
        lbl_sub = QLabel(subtitle)
        lbl_sub.setProperty("class", "page-sub")
        lbl_sub.setWordWrap(True)
        text_col.addWidget(lbl_title)
        text_col.addWidget(lbl_sub)

        if icon_name:
            pic = QLabel()
            pic.setPixmap(icon(icon_name, t["accent"], 30).pixmap(30, 30))
            pic.setFixedSize(30, 30)
            head.addWidget(pic, 0, Qt.AlignTop)

        layout.addLayout(head)

        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet("background: %s;" % t["border"])
        layout.addSpacing(4)
        layout.addWidget(line)
        layout.addSpacing(10)


class DirPage(_StepPage):
    def __init__(self, settings):
        super().__init__()
        self.setTitle("")
        self.setSubTitle("")
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        self._build_header(
            layout, 1, "folder", "选择保存目录",
            "抓取的小说会打包成 EPUB 存到这里；Markdown 索引可选，留空则与 EPUB 同目录。")

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.epub_edit = QLineEdit(settings.epub_root)
        self.epub_edit.setPlaceholderText("EPUB 保存目录（必填）")
        btn1 = QPushButton("浏览…")
        btn1.clicked.connect(lambda: self._pick(self.epub_edit))
        row1.addWidget(QLabel("EPUB"))
        row1.addWidget(self.epub_edit, 1)
        row1.addWidget(btn1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.md_edit = QLineEdit(settings.md_root)
        self.md_edit.setPlaceholderText("Markdown 索引目录（可选）")
        btn2 = QPushButton("浏览…")
        btn2.clicked.connect(lambda: self._pick(self.md_edit))
        row2.addWidget(QLabel("MD"))
        row2.addWidget(self.md_edit, 1)
        row2.addWidget(btn2)

        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addStretch(1)
        self.registerField("epub_root*", self.epub_edit)

    def _pick(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "选择目录", edit.text() or "")
        if path:
            edit.setText(path)

    def validatePage(self):
        self.settings.epub_root = self.epub_edit.text().strip()
        self.settings.md_root = self.md_edit.text().strip()
        return bool(self.settings.epub_root)


class CookiePage(_StepPage):
    def __init__(self, settings):
        super().__init__()
        self.setTitle("")
        self.setSubTitle("")
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        self._build_header(
            layout, 2, "key", "导入登录 Cookie（可选）",
            "未配置 Cookie 将无法抓取。也可以稍后在「设置 → 登录 Cookie」中补充。")

        self.cookie_edit = QPlainTextEdit()
        self.cookie_edit.setPlaceholderText("PHPSESSID=xxxx 或完整 Cookie 字符串")
        self.cookie_edit.setFixedHeight(84)
        layout.addWidget(self.cookie_edit)

        self.result_label = QLabel("")
        self.result_label.setProperty("class", "hint")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn = QPushButton("测试连接")
        btn.clicked.connect(self._test)
        btn_login = QPushButton("浏览器登录获取")
        btn_login.clicked.connect(self._browser_login)
        btn_row.addWidget(btn)
        btn_row.addWidget(btn_login)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        layout.addWidget(self.result_label)
        layout.addStretch(1)

    def _browser_login(self):
        from .login_window import launch_browser_login
        launch_browser_login(self, lambda: self.result_label.setText(
            "登录窗口已关闭，Cookie 已自动保存，可点击「测试连接」验证"))

    def _test(self):
        cookie = self.cookie_edit.toPlainText().strip()
        if not cookie:
            self.result_label.setText("请先粘贴 Cookie")
            return
        self.result_label.setText("测试中…")
        client = PixivClient(self.settings, cookie)
        ok, msg = client.test_connection()
        client.close()
        self.result_label.setProperty("class", "status-ok" if ok else "status-err")
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)
        self.result_label.setText(("✔ " if ok else "✘ ") + msg)

    def validatePage(self):
        cookie = self.cookie_edit.toPlainText().strip()
        if cookie:
            credentials.save_cookie(cookie)
        return True


class AuthorPage(_StepPage):
    def __init__(self, settings, db):
        super().__init__()
        self.setTitle("")
        self.setSubTitle("")
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        self._build_header(
            layout, 3, "users", "添加关注作者（可选）",
            "填入作者的数字 ID，多个之间用逗号或换行分隔；也可以先跳过，稍后在「作者」页补充。")

        self.ids_edit = QPlainTextEdit()
        self.ids_edit.setPlaceholderText("例如：1234567, 8901234")
        self.ids_edit.setFixedHeight(84)
        layout.addWidget(self.ids_edit)

        self.result_label = QLabel("")
        self.result_label.setProperty("class", "hint")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn = QPushButton("添加")
        btn.setProperty("class", "primary")
        btn.clicked.connect(self._add)
        btn_row.addWidget(btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        layout.addWidget(self.result_label)
        layout.addStretch(1)

    def _add(self):
        text = self.ids_edit.toPlainText()
        ids = [s for s in re.split(r"[\s,，、]+", text) if s.isdigit()]
        added = sum(1 for i in ids if self.db.add_author(i, pending_validate=True))
        self.result_label.setText(f"共 {len(ids)} 个：成功 {added}，重复 {len(ids) - added}")
        self.ids_edit.clear()


class RiskPage(_StepPage):
    def __init__(self, settings):
        super().__init__()
        self.setTitle("")
        self.setSubTitle("")
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        self._build_header(
            layout, 4, "shield", "风险确认",
            "请花一分钟读完下面的说明，勾选后即可开始使用。")

        box = QLabel(
            "本工具属于对 Pixiv 的自动化访问。Pixiv 服务条款禁止爬虫类聚合行为，"
            "实际执行以账号封禁、令牌吊销为主，使用本工具存在账号被限制的风险。\n\n"
            "降低风险的建议：仅抓取本人账号有权访问的作品；保持默认限速不小于 1.5 秒/次；"
            "不要将抓取的文件二次发布或用于商业用途。"
        )
        box.setWordWrap(True)
        box.setProperty("class", "soft")
        box.setContentsMargins(14, 12, 14, 12)
        box.setStyleSheet("QLabel { padding: 12px 14px; border-radius: 2px; }")
        layout.addWidget(box)

        self.agree = QCheckBox("我已知悉并自行承担风险")
        layout.addWidget(self.agree)
        layout.addStretch(1)
        self.registerField("risk*", self.agree)

    def validatePage(self):
        self.settings.risk_accepted = self.agree.isChecked()
        return True


class OnboardingWizard(QWizard):
    def __init__(self, settings, db, parent=None):
        super().__init__(parent)
        self.setWindowTitle("首次使用引导")
        self.settings = settings
        self.addPage(DirPage(settings))
        self.addPage(CookiePage(settings))
        self.addPage(AuthorPage(settings, db))
        self.addPage(RiskPage(settings))
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnLastPage, True)
        self.setButtonText(QWizard.BackButton, "上一步")
        self.setButtonText(QWizard.NextButton, "下一步")
        self.setButtonText(QWizard.CommitButton, "下一步")
        self.setButtonText(QWizard.FinishButton, "完成")
        self.setButtonText(QWizard.CancelButton, "取消")
        self.setPixmap(QWizard.LogoPixmap, QPixmap(app_mark(48)))
        self.resize(620, 470)

    def _tune_header(self):
        """ModernStyle 的标题标签由 Qt 内部创建，这里按对象名补上排版。"""
        t = tokens(current_mode())
        for lbl in self.findChildren(QLabel):
            name = lbl.objectName() or ""
            if name.endswith("subtitlelabel"):
                lbl.setStyleSheet("font-size: 12.5px; color: %s;" % t["text_dim"])
            elif name.endswith("titlelabel"):
                lbl.setStyleSheet("font-size: 19px; font-weight: 700; color: %s;" % t["text"])

    def showEvent(self, event):
        super().showEvent(event)
        self._tune_header()
