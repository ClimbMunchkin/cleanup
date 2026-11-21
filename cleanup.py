#!/usr/bin/env python3
"""
GUI для скрипта очистки системы (PyQt5).
Запуск: sudo -E python3 cleanup.py
"""

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QMessageBox, QProgressBar, QTextEdit, QFrame,
    QWidget, QSizePolicy, QCheckBox, QGroupBox, QGridLayout
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor


# ---------- Утилиты ----------
def run(cmd, shell=True):
    """Запуск команды, возврат (returncode, stdout+stderr)."""
    p = subprocess.run(
        cmd, shell=shell, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return p.returncode, p.stdout


def free_kb():
    total, used, free = shutil.disk_usage("/")
    return free // 1024


def human_mb(kb):
    mb = kb / 1024
    if abs(mb) >= 1024:
        return f"{mb/1024:.2f} GB"
    return f"{mb:.1f} MB"


# ---------- Стили ----------
STYLE = """
QDialog { background: #f7f8fa; }

QLabel#status {
    font-size: 9.5pt; color: #555555; padding: 4px 0;
}
QLabel#status[state="busy"] { color: #c0392b; font-weight: 600; }
QLabel#status[state="ok"]   { color: #27ae60; font-weight: 600; }
QLabel#status[state="err"]  { color: #c0392b; font-weight: 600; }

QFrame#card {
    background: #ffffff;
    border: 1px solid #e3e6ea;
    border-radius: 10px;
}

QGroupBox {
    background: #ffffff;
    border: 1px solid #e3e6ea;
    border-radius: 10px;
    margin-top: 14px;
    padding-top: 6px;
    font-size: 9.5pt;
    color: #555555;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    background: #f7f8fa;
}

QCheckBox {
    font-size: 10pt;
    color: #1a1a1a;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #b9bec6;
    border-radius: 4px;
    background: #ffffff;
}
QCheckBox::indicator:hover { border-color: #2d6cdf; }
QCheckBox::indicator:checked {
    background: #2d6cdf;
    border-color: #2d6cdf;
    image: none;
}
QCheckBox:disabled { color: #a0a0a0; }

QPushButton {
    background: #ffffff;
    border: 1px solid #d0d4da;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 10pt;
    color: #1a1a1a;
}
QPushButton:hover { background: #f2f5f9; border-color: #b9bec6; }
QPushButton:pressed { background: #e6eaef; }
QPushButton:disabled { color: #a0a0a0; border-color: #e3e6ea; }

QPushButton#primary {
    background: #2d6cdf; color: #ffffff; border: none;
    font-weight: 600;
}
QPushButton#primary:hover { background: #2459b8; }
QPushButton#primary:pressed { background: #1e4a99; }
QPushButton#primary:disabled { background: #a9bce0; color: #f0f0f0; }

QPushButton#danger {
    background: #ffffff; color: #c0392b; border: 1px solid #e5b9b3;
}
QPushButton#danger:hover { background: #fdf0ee; }

QProgressBar {
    background: #eef1f5;
    border: 1px solid #e3e6ea;
    border-radius: 4px;
    height: 10px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: #2d6cdf;
    border-radius: 4px;
}

QTextEdit {
    background: #1e1e1e;
    color: #d4d4d4;
    border: none;
    border-radius: 8px;
    padding: 8px;
    font-family: Consolas, monospace;
    font-size: 9.5pt;
}
"""


# ---------- Фоновый рабочий ----------
class CleanerWorker(QThread):
    progress = pyqtSignal(int, str)   # percent, status message
    log = pyqtSignal(str)
    finished_ok = pyqtSignal(int, int)  # freed_kb, after_kb
    finished_err = pyqtSignal(str)

    def __init__(self, tasks):
        super().__init__()
        # tasks: dict[str, bool] с ключами snap/flatpak/apt/journal/caches
        self.tasks = tasks

    def run(self):
        try:
            before = free_kb()
            self.log.emit(f"💾 Свободно ДО: {human_mb(before)}")

            # Собираем только выбранные шаги
            steps = []
            if self.tasks.get("snap"):
                steps.append(("Snap: старые ревизии", "Удаление старых snap-пакетов…", self._snap))
            if self.tasks.get("flatpak"):
                steps.append(("Flatpak", "Очистка flatpak…", self._flatpak))
            if self.tasks.get("apt"):
                steps.append(("Apt", "apt autoremove / clean…", self._apt))
            if self.tasks.get("journal"):
                steps.append(("Journal", "Очистка журналов systemd…", self._journal))
            if self.tasks.get("caches"):
                steps.append(("Кеши и /tmp", "Очистка кешей и /var/tmp…", self._caches))

            if not steps:
                self.log.emit("Ничего не выбрано — выходим.")
                after = free_kb()
                self.progress.emit(100, "Готово")
                self.finished_ok.emit(0, after)
                return

            n = len(steps)
            # Основная работа занимает диапазон 5..95 %, по шагам поровну.
            for i, (title, msg, fn) in enumerate(steps):
                start = 5 + int(i * 90 / n)
                self.progress.emit(start, msg)
                self.log.emit(f"\n=== {title} ===")
                fn()
                end = 5 + int((i + 1) * 90 / n)
                self.progress.emit(end, msg)

            self.progress.emit(97, "Подведение итогов…")
            after = free_kb()
            freed = after - before
            self.progress.emit(100, "Готово")
            self.log.emit("\n" + "=" * 40)
            self.log.emit(f"💾 Свободно ПОСЛЕ: {human_mb(after)}")
            if freed >= 0:
                self.log.emit(f"✅ Освобождено: {human_mb(freed)}")
            else:
                self.log.emit(f"⚠️  Свободного места стало меньше на {human_mb(-freed)}")
            self.log.emit("=" * 40)
            self.finished_ok.emit(freed, after)
        except Exception as e:
            self.finished_err.emit(str(e))

    # --- Шаги ---
    def _snap(self):
        code, out = run("LANG=en_US.UTF-8 snap list --all")
        if code != 0:
            self.log.emit("snap не установлен или недоступен, пропуск.")
            return
        lines = [l for l in out.splitlines() if "disabled" in l]
        if not lines:
            self.log.emit("Старых ревизий нет.")
            return
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue
            name, rev = parts[0], parts[2]
            self.log.emit(f"→ snap remove {name} --revision={rev}")
            c, o = run(f'sudo snap remove "{name}" --revision="{rev}"')
            if o.strip():
                self.log.emit(o.strip())

    def _flatpak(self):
        c, o = run("flatpak uninstall --unused --delete-data -y")
        self.log.emit(o.strip() or "нечего удалять")
        c, o = run("flatpak repair")
        self.log.emit(o.strip() or "repair: ок")

    def _apt(self):
        for cmd in ("sudo apt autoremove -y", "sudo apt clean"):
            self.log.emit(f"→ {cmd}")
            c, o = run(cmd)
            tail = "\n".join(o.strip().splitlines()[-8:])
            if tail:
                self.log.emit(tail)

    def _journal(self):
        c, o = run("sudo journalctl --vacuum-time=7d")
        if o.strip():
            self.log.emit(o.strip())

    def _caches(self):
        cache = os.path.expanduser("~/.cache")
        if os.path.isdir(cache):
            self.log.emit(f"→ rm -rf {cache}/*")
            for entry in os.listdir(cache):
                p = os.path.join(cache, entry)
                try:
                    if os.path.isdir(p) and not os.path.islink(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                except Exception as e:
                    self.log.emit(f"  ! {entry}: {e}")
        self.log.emit("→ sudo rm -rf /var/tmp/*")
        c, o = run("sudo rm -rf /var/tmp/*")
        if o.strip():
            self.log.emit(o.strip())


# ---------- Главное окно ----------
class CleanerApp(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Очистка системы Linux")
        self.resize(860, 700)
        self.setStyleSheet(STYLE)
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 12)
        root.setSpacing(10)

        # --- Прогресс ---
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        # --- Статус ---
        self.status_lbl = QLabel("Готов к запуску")
        self.status_lbl.setObjectName("status")
        root.addWidget(self.status_lbl)

        # --- Кнопки ---
        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.start_btn = QPushButton("▶  Запустить очистку")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start)

        self.clear_btn = QPushButton("Очистить лог")
        self.clear_btn.clicked.connect(self.clear_log)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.setObjectName("danger")
        self.close_btn.clicked.connect(self.reject)

        btns.addWidget(self.start_btn)
        btns.addWidget(self.clear_btn)
        btns.addStretch()
        btns.addWidget(self.close_btn)
        root.addLayout(btns)

        # --- Выбор компонентов ---
        sel_box = QGroupBox("Что очищать")
        sel_lay = QGridLayout(sel_box)
        sel_lay.setContentsMargins(12, 8, 12, 12)
        sel_lay.setHorizontalSpacing(18)
        sel_lay.setVerticalSpacing(6)

        self.cb = {}
        items = [
            ("snap",    "Snap: старые ревизии"),
            ("flatpak", "Flatpak: неиспользуемое + данные"),
            ("apt",     "Apt: autoremove + clean"),
            ("journal", "Journal: журналы > 7 дней"),
            ("caches",  "Кеши ~/.cache и /var/tmp"),
        ]
        for i, (key, text) in enumerate(items):
            cb = QCheckBox(text)
            cb.setChecked(True)
            self.cb[key] = cb
            sel_lay.addWidget(cb, i // 2, i % 2)

        root.addWidget(sel_box)

        # --- Карточка с логом ---
        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(10, 10, 10, 10)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        card_lay.addWidget(self.log)

        root.addWidget(card, 1)

        # --- Футер ---
        self.footer = QLabel("")
        self.footer.setObjectName("status")
        root.addWidget(self.footer)

    # --- Логирование ---
    def log_line(self, msg):
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(msg + "\n")
        self.log.moveCursor(QTextCursor.End)

    def clear_log(self):
        self.log.clear()

    def set_status(self, text, state=None):
        self.status_lbl.setText(text)
        if state:
            self.status_lbl.setProperty("state", state)
        else:
            self.status_lbl.setProperty("state", "")
        self.status_lbl.style().unpolish(self.status_lbl)
        self.status_lbl.style().polish(self.status_lbl)

    # --- Вспомогательное: блокировка выбора на время работы ---
    def _set_selection_enabled(self, enabled):
        for cb in self.cb.values():
            cb.setEnabled(enabled)
        self.start_btn.setEnabled(enabled)

    # --- Запуск ---
    def start(self):
        tasks = {k: cb.isChecked() for k, cb in self.cb.items()}
        if not any(tasks.values()):
            QMessageBox.information(self, "Ничего не выбрано",
                                    "Отметьте хотя бы один пункт для очистки.")
            return

        labels = {
            "snap":    "старые ревизии snap",
            "flatpak": "неиспользуемые flatpak и их данные",
            "apt":     "кеши apt (autoremove + clean)",
            "journal": "журналы systemd старше 7 дней",
            "caches":  "~/.cache/* и /var/tmp/*",
        }
        chosen = "\n".join(f" • {labels[k]}" for k, v in tasks.items() if v)

        if QMessageBox.question(
            self, "Подтверждение",
            f"Будут удалены:\n{chosen}\n\nПродолжить?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return

        self._set_selection_enabled(False)
        self.progress.setValue(0)
        self.set_status("Запуск...", "busy")

        self.worker = CleanerWorker(tasks)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self.log_line)
        self.worker.finished_ok.connect(self._on_finished_ok)
        self.worker.finished_err.connect(self._on_finished_err)
        self.worker.start()

    def _on_progress(self, percent, msg):
        self.progress.setValue(percent)
        self.set_status(msg, "busy")

    def _on_finished_ok(self, freed, after):
        self.set_status("Готово", "ok")
        if freed >= 0:
            self.footer.setText(f"Освобождено: {human_mb(freed)}   •   Свободно: {human_mb(after)}")
            QMessageBox.information(
                self, "Готово",
                f"Очистка завершена.\nОсвобождено: {human_mb(freed)}"
            )
        else:
            self.footer.setText(f"Занято больше на {human_mb(-freed)}   •   Свободно: {human_mb(after)}")
            QMessageBox.warning(
                self, "Готово",
                f"Очистка завершена.\nСвободного места стало меньше на {human_mb(-freed)}."
            )
        self._set_selection_enabled(True)

    def _on_finished_err(self, err):
        self.log_line(f"\n❌ Ошибка: {err}")
        self.set_status("Ошибка", "err")
        QMessageBox.critical(self, "Ошибка", err)
        self._set_selection_enabled(True)


def main():
    app = QApplication(sys.argv)
    dlg = CleanerApp()
    dlg.exec_()


if __name__ == "__main__":
    main()