from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWizard, QWizardPage,
)

from .. import credentials
from ..pixiv.client import PixivClient


class DirPage(QWizardPage):
    def __init__(self, settings):
        super().__init__()
        self.setTitle("第 1 步：选择保存目录")
        self.setSubTitle("EPUB 保存位置为必填；Markdown 索引留空则与 EPUB 同目录。")
        self.settings = settings
        layout = QVBoxLayout(self)

        row1 = QHBoxLayout()
        self.epub_edit = QLineEdit(settings.epub_root)
        self.epub_edit.setPlaceholderText("EPUB 保存目录（必填）")
        btn1 = QPushButton("浏览…")
        btn1.clicked.connect(lambda: self._pick(self.epub_edit))
        row1.addWidget(QLabel("EPUB："))
        row1.addWidget(self.epub_edit, 1)
        row1.addWidget(btn1)

        row2 = QHBoxLayout()
        self.md_edit = QLineEdit(settings.md_root)
        self.md_edit.setPlaceholderText("Markdown 索引目录（可选）")
        btn2 = QPushButton("浏览…")
        btn2.clicked.connect(lambda: self._pick(self.md_edit))
        row2.addWidget(QLabel("MD："))
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


class CookiePage(QWizardPage):
    def __init__(self, settings):
        super().__init__()
        self.setTitle("第 2 步：导入登录 Cookie（可选）")
        self.setSubTitle(
            "在浏览器登录 Pixiv 后，从开发者工具复制 Cookie 中的 PHPSESSID 字段粘贴到下方；"
            "可先跳过，稍后在「设置」页配置。未配置 Cookie 将无法抓取。"
        )
        self.settings = settings
        layout = QVBoxLayout(self)
        self.cookie_edit = QPlainTextEdit()
        self.cookie_edit.setPlaceholderText("PHPSESSID=xxxx 或完整 Cookie 字符串")
        self.cookie_edit.setMaximumHeight(90)
        self.result_label = QLabel("")
        btn = QPushButton("测试连接")
        btn.clicked.connect(self._test)
        btn_login = QPushButton("浏览器登录获取")
        btn_login.clicked.connect(self._browser_login)
        layout.addWidget(self.cookie_edit)
        layout.addWidget(btn, alignment=Qt.AlignLeft)
        layout.addWidget(btn_login, alignment=Qt.AlignLeft)
        layout.addWidget(self.result_label)
        layout.addStretch(1)

    def _browser_login(self):
        from .login_window import launch_browser_login
        launch_browser_login(self, lambda: self.result_label.setText("登录窗口已关闭，Cookie 已自动保存，可点击「测试连接」验证"))

    def _test(self):
        cookie = self.cookie_edit.toPlainText().strip()
        if not cookie:
            self.result_label.setText("请先粘贴 Cookie")
            return
        client = PixivClient(self.settings, cookie)
        ok, msg = client.test_connection()
        client.close()
        self.result_label.setText(("✔ " if ok else "✘ ") + msg)

    def validatePage(self):
        cookie = self.cookie_edit.toPlainText().strip()
        if cookie:
            credentials.save_cookie(cookie)
        return True


class AuthorPage(QWizardPage):
    def __init__(self, settings, db):
        super().__init__()
        self.setTitle("第 3 步：添加关注作者（可选）")
        self.setSubTitle("输入作者数字 ID，多个用逗号或换行分隔；也可跳过稍后添加。")
        self.db = db
        layout = QVBoxLayout(self)
        self.ids_edit = QPlainTextEdit()
        self.ids_edit.setPlaceholderText("例如：1234567, 8901234")
        self.result_label = QLabel("")
        btn = QPushButton("添加")
        btn.clicked.connect(self._add)
        layout.addWidget(self.ids_edit)
        layout.addWidget(btn, alignment=Qt.AlignLeft)
        layout.addWidget(self.result_label)
        layout.addStretch(1)

    def _add(self):
        import re
        text = self.ids_edit.toPlainText()
        ids = [s for s in re.split(r"[\s,，、]+", text) if s.isdigit()]
        added = sum(1 for i in ids if self.db.add_author(i, pending_validate=True))
        self.result_label.setText(f"共 {len(ids)} 个：成功 {added}，重复 {len(ids) - added}")
        self.ids_edit.clear()


class RiskPage(QWizardPage):
    def __init__(self, settings):
        super().__init__()
        self.setTitle("风险确认")
        self.settings = settings
        layout = QVBoxLayout(self)
        label = QLabel(
            "本工具属于对 Pixiv 的自动化访问。Pixiv 服务条款禁止爬虫类聚合行为，"
            "实际执行以账号封禁、令牌吊销为主，使用本工具存在账号被限制的风险。\n\n"
            "降低风险的建议：仅抓取本人账号有权访问的作品；保持默认限速不小于 1.5 秒/次；"
            "不要将抓取的文件二次发布或用于商业用途。\n\n"
            "勾选表示你已阅读并知悉上述风险。"
        )
        label.setWordWrap(True)
        self.agree = QCheckBox("我已知悉并自行承担风险")
        layout.addWidget(label)
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
        self.resize(560, 420)
