from __future__ import annotations

import concurrent.futures
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QCloseEvent, QDesktopServices, QFont
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .api_client import ApiTestResult, test_api_connection
from .credentials import CredentialError, CredentialStore
from .launcher import LaunchEvent, LaunchEventKind, PatchLauncher
from .models import (
    AppSettings,
    CUSTOM_PROMPT_MODES,
    DEFAULT_CUSTOM_PROMPT,
    PromptMode,
    ProviderCategory,
    ProviderId,
    QualityMode,
    TranslationMode,
)
from .providers import PROVIDERS, get_provider, make_launch_profile
from .settings import SettingsStore, app_data_directory, find_router_script


class UiSignals(QObject):
    launch_event = pyqtSignal(object)
    api_test_finished = pyqtSignal(object, object)


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings_store: SettingsStore | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        super().__init__()
        self.settings_store = settings_store or SettingsStore()
        self.credential_store = credential_store or CredentialStore()
        self.settings = self.settings_store.load()
        self.signals = UiSignals()
        self.signals.launch_event.connect(self._handle_launch_event)
        self.signals.api_test_finished.connect(self._handle_api_test_finished)
        self.launcher = PatchLauncher(self.signals.launch_event.emit)
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="api-connection-test"
        )
        self._loading = True
        self._api_test_in_progress = False
        self._close_after_stop = False
        self._current_provider_id = ""
        self._credential_cache: dict[str, dict[str, str]] = {}
        self._custom_prompt_slots = {
            PromptMode.CUSTOM1.value: DEFAULT_CUSTOM_PROMPT,
            PromptMode.CUSTOM2.value: DEFAULT_CUSTOM_PROMPT,
            PromptMode.CUSTOM3.value: DEFAULT_CUSTOM_PROMPT,
        }
        self._active_custom_slot: str | None = None
        self._prompt_ui_ready = False

        self.setWindowTitle(f"RenpyThief 非官方翻译补丁 · {__version__}")
        self.setMinimumSize(640, 600)
        self.resize(740, 700)
        self._build_ui()
        self._apply_style()
        self._load_settings_into_ui()
        self._loading = False
        self._update_mode_ui()
        self._set_status("idle", "未启动", "请选择翻译来源，然后启动 RenpyThief。")

    def _build_ui(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        central = QWidget(scroll)
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)

        title = QLabel("RenpyThief 非官方翻译补丁")
        title.setObjectName("title")
        subtitle = QLabel("不修改游戏、不修改系统代理；线路切换需要关闭并重新启动翻译器。")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self.status_frame = QFrame()
        self.status_frame.setObjectName("statusCard")
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(16, 13, 16, 13)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("statusDot")
        self.status_title = QLabel("未启动")
        self.status_title.setObjectName("statusTitle")
        self.status_detail = QLabel("")
        self.status_detail.setObjectName("statusDetail")
        self.status_detail.setWordWrap(True)
        status_text = QVBoxLayout()
        status_text.setSpacing(2)
        status_text.addWidget(self.status_title)
        status_text.addWidget(self.status_detail)
        status_layout.addWidget(self.status_dot, 0)
        status_layout.addLayout(status_text, 1)
        root.addWidget(self.status_frame)

        path_group = QGroupBox("原版程序")
        path_layout = QHBoxLayout(path_group)
        self.translator_path = QLineEdit()
        self.translator_path.setPlaceholderText("请选择 RenpyThief.exe")
        self.browse_button = QPushButton("浏览…")
        self.browse_button.clicked.connect(self._browse_translator)
        path_layout.addWidget(self.translator_path, 1)
        path_layout.addWidget(self.browse_button)
        root.addWidget(path_group)

        mode_group = QGroupBox("翻译来源")
        mode_layout = QGridLayout(mode_group)
        self.official_radio = QRadioButton("官方免费额度")
        self.custom_radio = QRadioButton("我的 API")
        self.official_radio.toggled.connect(self._update_mode_ui)
        self.custom_radio.toggled.connect(self._update_mode_ui)
        self.mode_help = QLabel("")
        self.mode_help.setObjectName("helpText")
        self.mode_help.setWordWrap(True)
        mode_layout.addWidget(self.official_radio, 0, 0)
        mode_layout.addWidget(self.custom_radio, 0, 1)
        mode_layout.addWidget(self.mode_help, 1, 0, 1, 2)
        root.addWidget(mode_group)

        self.api_group = QGroupBox("自定义翻译服务")
        api_layout = QFormLayout(self.api_group)
        api_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.provider_combo = QComboBox()
        for provider in PROVIDERS:
            self.provider_combo.addItem(provider.label, provider.provider_id.value)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.provider_description = QLabel("")
        self.provider_description.setObjectName("helpText")
        self.provider_description.setWordWrap(True)
        self.model_label = QLabel("模型")
        self.model_edit = QLineEdit()
        self.quality_label = QLabel("质量")
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("极速（推荐）", QualityMode.FAST.value)
        self.quality_combo.addItem("高质量（启用思考）", QualityMode.HIGH.value)
        self.credential_labels: list[QLabel] = []
        self.credential_edits: list[QLineEdit] = []
        self.credential_show_checks: list[QCheckBox] = []
        self.credential_rows: list[QWidget] = []
        for _index in range(2):
            field_label = QLabel("")
            field_edit = QLineEdit()
            show_field = QCheckBox("显示")
            show_field.toggled.connect(
                lambda checked, edit=field_edit: edit.setEchoMode(
                    QLineEdit.Normal if checked else QLineEdit.Password
                )
            )
            field_row = QWidget()
            field_layout = QHBoxLayout(field_row)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.addWidget(field_edit, 1)
            field_layout.addWidget(show_field)
            self.credential_labels.append(field_label)
            self.credential_edits.append(field_edit)
            self.credential_show_checks.append(show_field)
            self.credential_rows.append(field_row)
        # Compatibility aliases for the existing AI-key workflow.
        self.api_key_edit = self.credential_edits[0]
        self.show_key = self.credential_show_checks[0]
        self.remember_key = QCheckBox("使用 Windows 凭据管理器安全保存")
        self.test_api_button = QPushButton("测试 API")
        self.test_api_button.clicked.connect(self._test_api)
        api_layout.addRow("Provider", self.provider_combo)
        api_layout.addRow("", self.provider_description)
        api_layout.addRow(self.model_label, self.model_edit)
        api_layout.addRow(self.quality_label, self.quality_combo)
        for label, row in zip(self.credential_labels, self.credential_rows):
            api_layout.addRow(label, row)
        api_layout.addRow("", self.remember_key)
        api_layout.addRow("", self.test_api_button)
        root.addWidget(self.api_group)

        self.prompt_group = QGroupBox("AI 翻译提示词")
        prompt_layout = QFormLayout(self.prompt_group)
        prompt_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.prompt_combo = QComboBox()
        self.prompt_combo.addItem("模板 1 · 简洁直译", PromptMode.TEMPLATE1.value)
        self.prompt_combo.addItem("模板 2 · 游戏本地化", PromptMode.TEMPLATE2.value)
        self.prompt_combo.addItem("自定义 1", PromptMode.CUSTOM1.value)
        self.prompt_combo.addItem("自定义 2", PromptMode.CUSTOM2.value)
        self.prompt_combo.addItem("自定义 3", PromptMode.CUSTOM3.value)
        self.prompt_combo.currentIndexChanged.connect(self._update_prompt_ui)
        self.custom_prompt_label = QLabel("自定义 1 内容")
        self.custom_prompt_edit = QTextEdit()
        self.custom_prompt_edit.setAcceptRichText(False)
        self.custom_prompt_edit.setMinimumHeight(110)
        self.custom_prompt_edit.setPlaceholderText(
            "可使用 {source}、{target}、{text}。如果省略 {text}，系统会自动追加原文。"
        )
        prompt_help = QLabel(
            "模板由补丁维护；三个自定义槽位会分别保存。支持 {source}、{target} 和 {text}。"
        )
        prompt_help.setObjectName("helpText")
        prompt_help.setWordWrap(True)
        prompt_layout.addRow("模式", self.prompt_combo)
        prompt_layout.addRow(self.custom_prompt_label, self.custom_prompt_edit)
        prompt_layout.addRow("", prompt_help)
        root.addWidget(self.prompt_group)

        update_group = QGroupBox("兼容性保护")
        update_layout = QVBoxLayout(update_group)
        self.block_updates_checkbox = QCheckBox("启用兼容性保护（推荐）")
        self.block_updates_checkbox.clicked.connect(self._block_updates_clicked)
        update_help = QLabel(
            "默认开启。拦截已知版本检查；在「我的 API」下还会本地应答登录/心跳/注入上报，"
            "并在原版目录缺少登录记录时写入仅用于本机的会话标记，同时拒绝官方游戏配置下载。"
            "不会覆盖已有登录记录，也不会覆盖 RenpyThief.exe。"
        )
        update_help.setObjectName("helpText")
        update_help.setWordWrap(True)
        update_layout.addWidget(self.block_updates_checkbox)
        update_layout.addWidget(update_help)
        root.addWidget(update_group)

        self.advanced_group = QGroupBox("高级设置")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        advanced_outer = QVBoxLayout(self.advanced_group)
        self.advanced_content = QWidget()
        advanced_layout = QFormLayout(self.advanced_content)
        self.base_url_label = QLabel("API Base URL")
        self.base_url_edit = QLineEdit()
        self.bridge_concurrency = self._make_spin(1, 128)
        self.upstream_concurrency = self._make_spin(1, 128)
        self.cache_entries = self._make_spin(0, 1_000_000)
        self.cache_mebibytes = self._make_spin(0, 1024, " MiB")
        advanced_layout.addRow(self.base_url_label, self.base_url_edit)
        advanced_layout.addRow("本地并发", self.bridge_concurrency)
        advanced_layout.addRow("上游并发", self.upstream_concurrency)
        advanced_layout.addRow("内存缓存条目", self.cache_entries)
        advanced_layout.addRow("内存缓存上限", self.cache_mebibytes)
        advanced_note = QLabel(
            "端口、注入参数和动态监听识别由补丁自动管理，不提供手动修改。"
        )
        advanced_note.setObjectName("helpText")
        advanced_note.setWordWrap(True)
        advanced_layout.addRow("", advanced_note)
        advanced_outer.addWidget(self.advanced_content)
        self.advanced_content.setVisible(False)
        self.advanced_group.toggled.connect(self.advanced_content.setVisible)
        root.addWidget(self.advanced_group)

        actions = QHBoxLayout()
        self.start_button = QPushButton("启动原版翻译器")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.setEnabled(False)
        actions.addWidget(self.start_button, 1)
        actions.addWidget(self.stop_button)
        root.addLayout(actions)

        log_header = QHBoxLayout()
        log_label = QLabel("运行信息（不记录游戏正文）")
        log_label.setObjectName("sectionLabel")
        self.open_diagnostics_button = QPushButton("打开诊断目录")
        self.open_diagnostics_button.clicked.connect(self._open_diagnostics)
        log_header.addWidget(log_label, 1)
        log_header.addWidget(self.open_diagnostics_button)
        root.addLayout(log_header)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(300)
        self.log_view.setMinimumHeight(116)
        root.addWidget(self.log_view, 1)

        footer = QLabel("非官方社区补丁 · 原版文件不会被覆盖 · 不会静默切换到付费 API")
        footer.setObjectName("footer")
        footer.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        root.addWidget(footer)

    def _make_spin(
        self, minimum: int, maximum: int, suffix: str = ""
    ) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setButtonSymbols(QAbstractSpinBox.PlusMinus)
        if suffix:
            widget.setSuffix(suffix)
        return widget

    def _apply_style(self) -> None:
        self.setFont(QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f5f7fb; color: #172033; }
            QLabel#title { font-size: 24px; font-weight: 700; color: #111827; }
            QLabel#subtitle, QLabel#helpText, QLabel#footer { color: #667085; }
            QLabel#footer { font-size: 9px; }
            QLabel#sectionLabel { font-weight: 600; }
            QFrame#statusCard { background: #ffffff; border: 1px solid #dce3ef; border-radius: 10px; }
            QLabel#statusDot { color: #98a2b3; font-size: 18px; padding-right: 5px; }
            QLabel#statusTitle { font-weight: 700; }
            QLabel#statusDetail { color: #667085; }
            QGroupBox { background: #ffffff; border: 1px solid #dce3ef; border-radius: 10px;
                        margin-top: 10px; padding: 12px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 5px; }
            QLineEdit, QComboBox, QSpinBox, QTextEdit { background: #ffffff; border: 1px solid #cfd7e6;
                        border-radius: 6px; padding: 7px; selection-background-color: #2563eb; }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #2563eb; }
            QPushButton { background: #ffffff; border: 1px solid #c7d0df; border-radius: 6px;
                          padding: 7px 13px; }
            QPushButton:hover { background: #eef4ff; border-color: #8eb1f0; }
            QPushButton:disabled { color: #98a2b3; background: #f2f4f7; }
            QPushButton#primaryButton { background: #2563eb; color: white; border: 1px solid #2563eb;
                                        font-weight: 700; padding: 10px 18px; }
            QPushButton#primaryButton:hover { background: #1d4ed8; }
            QRadioButton { spacing: 7px; font-weight: 600; }
            QCheckBox { font-weight: 400; }
            """
        )

    def _load_settings_into_ui(self) -> None:
        settings = self.settings
        self.translator_path.setText(settings.translator_path)
        self.official_radio.setChecked(settings.mode == TranslationMode.OFFICIAL.value)
        self.custom_radio.setChecked(settings.mode == TranslationMode.CUSTOM.value)
        provider_index = self.provider_combo.findData(settings.provider)
        self.provider_combo.setCurrentIndex(max(0, provider_index))
        self._current_provider_id = str(self.provider_combo.currentData())
        self.base_url_edit.setText(settings.base_url)
        self.model_edit.setText(settings.model)
        quality_index = self.quality_combo.findData(settings.quality)
        self.quality_combo.setCurrentIndex(max(0, quality_index))
        prompt_index = self.prompt_combo.findData(settings.prompt_mode)
        self._prompt_ui_ready = False
        self._custom_prompt_slots = {
            PromptMode.CUSTOM1.value: settings.custom_prompt_1,
            PromptMode.CUSTOM2.value: settings.custom_prompt_2,
            PromptMode.CUSTOM3.value: settings.custom_prompt_3,
        }
        self._active_custom_slot = None
        self.prompt_combo.setCurrentIndex(max(0, prompt_index))
        self._prompt_ui_ready = True
        self._update_prompt_ui()
        self.block_updates_checkbox.setChecked(settings.block_updates)
        self.remember_key.setChecked(settings.remember_api_key)
        self.bridge_concurrency.setValue(settings.bridge_concurrency)
        self.upstream_concurrency.setValue(settings.upstream_concurrency)
        self.cache_entries.setValue(settings.cache_entries)
        self.cache_mebibytes.setValue(settings.cache_mebibytes)
        self._refresh_provider_description()
        self._update_prompt_ui()
        if settings.remember_api_key:
            self._load_saved_credentials(settings.provider)

    def _collect_settings(self) -> AppSettings:
        mode = (
            TranslationMode.CUSTOM.value
            if self.custom_radio.isChecked()
            else TranslationMode.OFFICIAL.value
        )
        self._store_active_custom_prompt()
        value = AppSettings(
            translator_path=self.translator_path.text().strip(),
            mode=mode,
            provider=str(self.provider_combo.currentData()),
            base_url=self.base_url_edit.text().strip(),
            model=self.model_edit.text().strip(),
            quality=str(self.quality_combo.currentData()),
            prompt_mode=str(self.prompt_combo.currentData()),
            custom_prompt_1=self._custom_prompt_slots[PromptMode.CUSTOM1.value],
            custom_prompt_2=self._custom_prompt_slots[PromptMode.CUSTOM2.value],
            custom_prompt_3=self._custom_prompt_slots[PromptMode.CUSTOM3.value],
            block_updates=self.block_updates_checkbox.isChecked(),
            remember_api_key=self.remember_key.isChecked(),
            bridge_concurrency=self.bridge_concurrency.value(),
            upstream_concurrency=self.upstream_concurrency.value(),
            cache_entries=self.cache_entries.value(),
            cache_mebibytes=self.cache_mebibytes.value(),
        )
        value.normalize()
        if value.upstream_concurrency > value.bridge_concurrency:
            raise ValueError("上游并发不能高于本地并发。")
        if mode == TranslationMode.CUSTOM.value:
            provider = get_provider(value.provider)
            if (
                provider.category is ProviderCategory.AI
                and value.prompt_mode in CUSTOM_PROMPT_MODES
                and not value.selected_custom_prompt().strip()
            ):
                raise ValueError("自定义提示词不能为空。")
            make_launch_profile(value)
        return value

    def _browse_translator(self) -> None:
        current = self.translator_path.text().strip()
        directory = str(Path(current).parent) if current else str(Path.home())
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择 RenpyThief.exe",
            directory,
            "RenpyThief (RenpyThief.exe);;可执行文件 (*.exe)",
        )
        if selected:
            self.translator_path.setText(selected)

    def _provider_changed(self) -> None:
        provider = get_provider(str(self.provider_combo.currentData()))
        if not self._loading:
            self._stash_visible_credentials()
            self._current_provider_id = provider.provider_id.value
            self.base_url_edit.setText(provider.base_url)
            self.model_edit.setText(provider.model)
            values = self._credential_cache.get(provider.provider_id.value)
            if values is None and self.remember_key.isChecked():
                values = self._read_saved_credentials(provider.provider_id.value)
            self._show_credential_values(provider.provider_id.value, values or {})
        self._refresh_provider_description()

    def _refresh_provider_description(self) -> None:
        provider = get_provider(str(self.provider_combo.currentData()))
        self.provider_description.setText(provider.description)
        is_ai = provider.category is ProviderCategory.AI
        self.model_label.setVisible(is_ai)
        self.model_edit.setVisible(is_ai)
        self.quality_label.setVisible(is_ai)
        self.quality_combo.setVisible(is_ai)
        self.base_url_label.setVisible(is_ai)
        self.base_url_edit.setVisible(is_ai)
        self.quality_combo.setEnabled(provider.supports_quality_modes)
        if not provider.supports_quality_modes:
            self.quality_combo.setCurrentIndex(
                max(0, self.quality_combo.findData(QualityMode.FAST.value))
            )
        self._configure_credential_rows(provider.provider_id.value)
        if provider.provider_id in {
            ProviderId.OPENAI_COMPATIBLE,
            ProviderId.LOCAL_OPENAI,
        }:
            self.advanced_group.setChecked(True)
            self.base_url_edit.setPlaceholderText(
                "例如 http://127.0.0.1:11434/v1 或 http://127.0.0.1:8080/v1"
            )
        else:
            self.base_url_edit.setPlaceholderText("")
        self.test_api_button.setEnabled(
            provider.network_ready
            and not self.launcher.running
            and not self._api_test_in_progress
        )
        self.test_api_button.setToolTip(
            "" if provider.network_ready else "此平台当前仅预留配置，尚未接入网络调用。"
        )
        self._update_prompt_ui()

    def _read_saved_credentials(self, provider_id: str) -> dict[str, str]:
        provider = get_provider(provider_id)
        try:
            if provider.category is ProviderCategory.AI:
                value = self.credential_store.get(provider_id)
                return {"api_key": value} if value else {}
            return self.credential_store.get_bundle(provider_id)
        except CredentialError as error:
            self._append_log(str(error))
            return {}

    def _load_saved_credentials(self, provider_id: str) -> None:
        values = self._read_saved_credentials(provider_id)
        self._credential_cache[provider_id] = values
        self._show_credential_values(provider_id, values)

    def _configure_credential_rows(self, provider_id: str) -> None:
        provider = get_provider(provider_id)
        for index, (label, edit, show, row) in enumerate(
            zip(
                self.credential_labels,
                self.credential_edits,
                self.credential_show_checks,
                self.credential_rows,
            )
        ):
            visible = index < len(provider.credential_fields)
            label.setVisible(visible)
            row.setVisible(visible)
            if not visible:
                edit.clear()
                continue
            field = provider.credential_fields[index]
            label.setText(field.label)
            edit.setPlaceholderText(
                field.placeholder
                or ("不会写入设置文件或命令行" if field.secret else "")
            )
            show.setChecked(False)
            show.setVisible(field.secret)
            edit.setEchoMode(QLineEdit.Password if field.secret else QLineEdit.Normal)

    def _stash_visible_credentials(self) -> None:
        if not self._current_provider_id:
            return
        provider = get_provider(self._current_provider_id)
        self._credential_cache[self._current_provider_id] = {
            field.key: self.credential_edits[index].text().strip()
            for index, field in enumerate(provider.credential_fields)
        }

    def _show_credential_values(
        self, provider_id: str, values: dict[str, str]
    ) -> None:
        provider = get_provider(provider_id)
        for edit in self.credential_edits:
            edit.clear()
        for index, field in enumerate(provider.credential_fields):
            self.credential_edits[index].setText(str(values.get(field.key, "")))

    def _collect_credentials(self) -> dict[str, str]:
        provider = get_provider(str(self.provider_combo.currentData()))
        values = {
            field.key: self.credential_edits[index].text().strip()
            for index, field in enumerate(provider.credential_fields)
        }
        missing = [
            field.label
            for field in provider.credential_fields
            if not field.optional and not values[field.key]
        ]
        if missing:
            raise ValueError("请填写：" + "、".join(missing) + "。")
        for value in values.values():
            if any(character.isspace() for character in value):
                raise ValueError("凭据字段不能包含空格或换行。")
        self._credential_cache[provider.provider_id.value] = values
        return values

    def _store_active_custom_prompt(self) -> None:
        if self._active_custom_slot and hasattr(self, "custom_prompt_edit"):
            self._custom_prompt_slots[self._active_custom_slot] = (
                self.custom_prompt_edit.toPlainText()
            )

    def _update_prompt_ui(self) -> None:
        if not hasattr(self, "prompt_group"):
            return
        provider = get_provider(str(self.provider_combo.currentData()))
        visible = (
            self.custom_radio.isChecked()
            and provider.category is ProviderCategory.AI
        )
        self.prompt_group.setVisible(visible)
        mode = str(self.prompt_combo.currentData() or "")
        if self._prompt_ui_ready:
            self._store_active_custom_prompt()
        custom = mode in CUSTOM_PROMPT_MODES
        self.custom_prompt_label.setVisible(custom)
        self.custom_prompt_edit.setVisible(custom)
        if custom:
            slot = {"custom1": "1", "custom2": "2", "custom3": "3"}.get(mode, "1")
            self.custom_prompt_label.setText(f"自定义 {slot} 内容")
            self.custom_prompt_edit.setPlainText(
                self._custom_prompt_slots.get(mode, DEFAULT_CUSTOM_PROMPT)
            )
            self._active_custom_slot = mode
        else:
            self._active_custom_slot = None
        self.prompt_group.setEnabled(visible and not self.launcher.running)

    def _block_updates_clicked(self, checked: bool) -> None:
        if checked:
            return
        answer = QMessageBox.warning(
            self,
            "确认关闭兼容性保护？",
            "关闭后，RenpyThief 可能自动更新，且「我的 API」将重新依赖官方会话接口。"
            "新版可能导致补丁失效，也可能覆盖或清除补丁组件。\n\n仍要关闭兼容性保护吗？",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            self.block_updates_checkbox.setChecked(True)

    def _update_mode_ui(self) -> None:
        if not hasattr(self, "api_group"):
            return
        custom = self.custom_radio.isChecked()
        self.api_group.setVisible(custom)
        self.advanced_group.setVisible(custom)
        self.api_group.setEnabled(custom and not self.launcher.running)
        self.advanced_group.setEnabled(custom and not self.launcher.running)
        self._update_prompt_ui()
        if custom:
            self.mode_help.setText(
                "翻译请求会转发到你选择的 API（含本机 127.0.0.1 上的 OpenAI 兼容服务）；"
                "可能产生费用。官方会话接口由兼容性保护在进程内应答；"
                "只有路由确认后才会提示拖入游戏。"
            )
            self.start_button.setText("使用我的 API 启动")
        else:
            self.mode_help.setText(
                "直接启动原版，不运行桥接、不注入路由，也不会读取或使用你的 API Key。"
            )
            self.start_button.setText("启动原版翻译器")

    def _save_credentials(
        self, settings: AppSettings, values: dict[str, str]
    ) -> None:
        provider_id = settings.provider
        provider = get_provider(provider_id)
        if settings.remember_api_key:
            if provider.category is ProviderCategory.AI:
                self.credential_store.set(provider_id, values.get("api_key", ""))
            else:
                self.credential_store.set_bundle(provider_id, values)
        else:
            self.credential_store.delete(provider_id)

    def _start(self) -> None:
        try:
            settings = self._collect_settings()
            credentials: dict[str, str] = {}
            if settings.mode == TranslationMode.CUSTOM.value:
                credentials = self._collect_credentials()
                self._save_credentials(settings, credentials)
            self.settings_store.save(settings)
            self.settings = settings
            self.launcher.start(settings, credentials)
        except (OSError, ValueError, RuntimeError, CredentialError) as error:
            QMessageBox.critical(self, "无法启动", str(error))
            self._set_status("error", "启动失败", str(error))
            self._append_log(f"启动失败：{error}")
            return
        self._set_controls_running(True)

    def _stop(self) -> None:
        if self.launcher.running:
            self.launcher.request_stop()

    def _test_api(self) -> None:
        if self._api_test_in_progress:
            return
        try:
            settings = self._collect_settings()
            settings.mode = TranslationMode.CUSTOM.value
            make_launch_profile(settings)
            credentials = self._collect_credentials()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "无法测试", str(error))
            return
        self._api_test_in_progress = True
        self.test_api_button.setEnabled(False)
        self.test_api_button.setText("正在测试…")
        self._append_log("正在发送固定测试文本；不会发送游戏内容。")
        future = self.executor.submit(test_api_connection, settings, credentials)

        def completed(value: concurrent.futures.Future[ApiTestResult]) -> None:
            try:
                result = value.result()
                self.signals.api_test_finished.emit(result, None)
            except Exception as error:  # The UI displays only the sanitized message.
                self.signals.api_test_finished.emit(None, error)

        future.add_done_callback(completed)

    def _handle_api_test_finished(
        self, result: ApiTestResult | None, error: Exception | None
    ) -> None:
        self._api_test_in_progress = False
        self.test_api_button.setEnabled(not self.launcher.running)
        self.test_api_button.setText("测试 API")
        if error is not None:
            self._append_log(f"API 测试失败：{error}")
            QMessageBox.warning(self, "API 测试失败", str(error))
            return
        assert result is not None
        self._append_log(f"API 测试成功，耗时 {result.elapsed_ms:.0f} ms。")
        QMessageBox.information(
            self,
            "API 测试成功",
            f"连接与响应格式正常。\n耗时：{result.elapsed_ms:.0f} ms\n测试译文：{result.translated_text}",
        )

    def _handle_launch_event(self, event: LaunchEvent) -> None:
        self._append_log(event.message)
        if event.kind is LaunchEventKind.STARTING:
            self._set_status("starting", "正在启动", event.message)
            self._set_controls_running(True)
        elif event.kind is LaunchEventKind.READY:
            active = "官方额度" if self.official_radio.isChecked() else "我的 API"
            self._set_status("ready", f"{active} · 已就绪", event.message)
        elif event.kind is LaunchEventKind.WARNING:
            self._set_status("warning", "更新保护未确认", event.message)
            QMessageBox.warning(self, "更新保护未确认", event.message)
        elif event.kind is LaunchEventKind.STOPPING:
            self._set_status("starting", "正在停止", event.message)
            self.stop_button.setEnabled(False)
        elif event.kind is LaunchEventKind.EXITED:
            self._set_status("idle", "未启动", event.message)
            self._set_controls_running(False)
            if self._close_after_stop:
                self._close_after_stop = False
                QTimer.singleShot(0, self.close)
        elif event.kind is LaunchEventKind.ERROR:
            self._set_status("error", "运行错误", event.message)
            self._set_controls_running(False)
            QMessageBox.critical(self, "运行错误", event.message)

    def _set_controls_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.translator_path.setEnabled(not running)
        self.browse_button.setEnabled(not running)
        self.official_radio.setEnabled(not running)
        self.custom_radio.setEnabled(not running)
        self.api_group.setEnabled(not running and self.custom_radio.isChecked())
        self.advanced_group.setEnabled(not running and self.custom_radio.isChecked())
        self.prompt_group.setEnabled(not running and self.custom_radio.isChecked())
        self.block_updates_checkbox.setEnabled(not running)
        provider = get_provider(str(self.provider_combo.currentData()))
        self.test_api_button.setEnabled(
            not running and not self._api_test_in_progress and provider.network_ready
        )

    def _set_status(self, state: str, title: str, detail: str) -> None:
        colors = {
            "idle": "#98a2b3",
            "starting": "#f59e0b",
            "ready": "#16a34a",
            "warning": "#f59e0b",
            "error": "#dc2626",
        }
        self.status_dot.setStyleSheet(f"color: {colors.get(state, colors['idle'])};")
        self.status_title.setText(title)
        self.status_detail.setText(detail)

    def _append_log(self, message: str) -> None:
        self.log_view.append(message.replace("\r", " ").replace("\n", " "))

    def _open_diagnostics(self) -> None:
        router = find_router_script()
        path = router.parent if router is not None else app_data_directory()
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.launcher.running:
            answer = QMessageBox.question(
                self,
                "翻译器仍在运行",
                "必须先关闭 RenpyThief，才能安全停止本地桥接。\n"
                "是否现在请求正常关闭并退出补丁？",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._close_after_stop = True
            self.launcher.request_stop()
            event.ignore()
            return
        self.executor.shutdown(wait=False, cancel_futures=True)
        event.accept()


def center_window(window: MainWindow) -> None:
    screen = QApplication.primaryScreen()
    if screen is None:
        return
    geometry = screen.availableGeometry()
    frame = window.frameGeometry()
    frame.moveCenter(geometry.center())
    window.move(frame.topLeft())
