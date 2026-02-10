#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import re
import urllib.request
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QSettings, QSize, QUrl
)
from PyQt6.QtGui import QPixmap, QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QLineEdit, QPushButton,
    QFileDialog, QCheckBox, QTextEdit, QProgressBar, QSplitter,
    QMessageBox, QComboBox, QFrame, QSizePolicy
)


INDEX_URL_DEFAULT = "https://json.tikey.art/index.json"
BASE_URL_DEFAULT  = "https://json.tikey.art/"  # per risolvere path relativi file/preview


# -----------------------------
# Helpers: URL + safe filenames
# -----------------------------

def join_url(base: str, path_or_url: str) -> str:
    """Se è già URL assoluto, torna quello; altrimenti unisce base + path relativo."""
    if not path_or_url:
        return ""
    p = path_or_url.strip()
    if p.startswith("http://") or p.startswith("https://"):
        return p
    base = base.rstrip("/") + "/"
    return urllib.parse.urljoin(base, p.lstrip("/"))

def safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "file"

def guess_ext_from_url(url: str, default: str = ".json") -> str:
    try:
        path = urllib.parse.urlparse(url).path
        _, ext = os.path.splitext(path)
        return ext if ext else default
    except Exception:
        return default


# -----------------------------
# Catalog item model
# -----------------------------

@dataclass
class CatalogItem:
    title: str
    json_url: str
    preview_url: str
    description: str
    tags: List[str]
    updated: str

def _get_first(d: Dict[str, Any], keys: List[str], default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default

def _get_tags(d: Dict[str, Any]) -> List[str]:
    for k in ["tags", "tag", "keywords", "labels"]:
        v = d.get(k)
        if isinstance(v, list):
            out = []
            for x in v:
                if isinstance(x, str) and x.strip():
                    out.append(x.strip())
            return out
        if isinstance(v, str) and v.strip():
            # consentiamo tag separati da virgole
            return [t.strip() for t in v.split(",") if t.strip()]
    return []

def parse_catalog(raw: Dict[str, Any], base_url: str) -> List[CatalogItem]:
    items = raw.get("items", raw.get("worlds", raw.get("data", [])))
    if not isinstance(items, list):
        items = []

    out: List[CatalogItem] = []
    for it in items:
        if not isinstance(it, dict):
            continue

        title = _get_first(it, ["name", "title", "world", "id"], default="(untitled)")
        file_path = _get_first(it, ["file", "json", "path", "url", "json_url"], default="")
        preview_path = _get_first(it, ["preview", "image", "thumb", "thumbnail", "preview_url", "image_url"], default="")
        desc = _get_first(it, ["description", "desc", "about", "notes"], default="")
        updated = _get_first(it, ["updated", "date", "modified"], default="")
        tags = _get_tags(it)

        json_url = join_url(base_url, file_path)
        preview_url = join_url(base_url, preview_path)

        # scarta roba senza json_url
        if not json_url:
            continue

        out.append(CatalogItem(
            title=title,
            json_url=json_url,
            preview_url=preview_url,
            description=desc,
            tags=tags,
            updated=updated
        ))

    return out


# -----------------------------
# Networking workers (QThread)
# -----------------------------

class FetchCatalogWorker(QThread):
    ok = pyqtSignal(list)
    fail = pyqtSignal(str)

    def __init__(self, index_url: str, base_url: str):
        super().__init__()
        self.index_url = index_url
        self.base_url = base_url

    def run(self):
        try:
            req = urllib.request.Request(
                self.index_url,
                headers={"User-Agent": "TK-JSON-Downloader/1.0"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read().decode("utf-8", errors="replace")
            raw = json.loads(data)
            items = parse_catalog(raw, self.base_url)
            self.ok.emit(items)
        except Exception as e:
            self.fail.emit(str(e))


class DownloadWorker(QThread):
    progress = pyqtSignal(int, int, str)  # done, total, current_title
    log = pyqtSignal(str)
    done = pyqtSignal()
    fail = pyqtSignal(str)

    def __init__(self, items: List[CatalogItem], out_dir: str, also_preview: bool):
        super().__init__()
        self.items = items
        self.out_dir = out_dir
        self.also_preview = also_preview

    def _download_to(self, url: str, path: str):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TK-JSON-Downloader/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def run(self):
        try:
            total = len(self.items)
            done = 0
            os.makedirs(self.out_dir, exist_ok=True)

            for it in self.items:
                title = it.title
                self.log.emit(f"• Scarico: {title}")

                # JSON
                json_ext = guess_ext_from_url(it.json_url, ".json")
                json_name = safe_filename(title) + json_ext
                json_path = os.path.join(self.out_dir, "json", json_name)
                self._download_to(it.json_url, json_path)
                self.log.emit(f"  ✓ JSON → {json_path}")

                # Preview (opzionale)
                if self.also_preview and it.preview_url:
                    img_ext = guess_ext_from_url(it.preview_url, ".webp")
                    img_name = safe_filename(title) + img_ext
                    img_path = os.path.join(self.out_dir, "preview", img_name)
                    self._download_to(it.preview_url, img_path)
                    self.log.emit(f"  ✓ Preview → {img_path}")

                done += 1
                self.progress.emit(done, total, title)

            self.log.emit("— Finito. Albero pulito. 🖤")
            self.done.emit()
        except Exception as e:
            self.fail.emit(str(e))


# -----------------------------
# UI
# -----------------------------

DARK_DANTE_QSS = """
/* Dark Dante — sobrio, elegante, gotico */
QMainWindow, QWidget {
  background: #0b0b10;
  color: #e8e8f0;
  font-family: "DejaVu Sans";
  font-size: 12px;
}
QLineEdit, QComboBox, QTextEdit {
  background: #11111a;
  border: 1px solid #2a2a3b;
  border-radius: 10px;
  padding: 8px;
  selection-background-color: #5b2b73;
}
QTextEdit { border-radius: 12px; }
QPushButton {
  background: #151522;
  border: 1px solid #2a2a3b;
  border-radius: 12px;
  padding: 8px 12px;
}
QPushButton:hover {
  border-color: #7b3ea1;
}
QPushButton:pressed {
  background: #10101a;
}
QCheckBox { spacing: 8px; }
QProgressBar {
  background: #11111a;
  border: 1px solid #2a2a3b;
  border-radius: 10px;
  height: 18px;
  text-align: center;
}
QProgressBar::chunk {
  background-color: #5b2b73;
  border-radius: 10px;
}
QListWidget {
  background: #0f0f18;
  border: 1px solid #2a2a3b;
  border-radius: 12px;
  padding: 6px;
}
QListWidget::item {
  padding: 8px;
  border-radius: 10px;
}
QListWidget::item:selected {
  background: #1c1024;
  border: 1px solid #7b3ea1;
}
QLabel#Title {
  font-size: 16px;
  font-weight: 600;
}
QLabel#Subtle {
  color: #b7b7c8;
}
QFrame#Panel {
  background: #0f0f18;
  border: 1px solid #2a2a3b;
  border-radius: 14px;
}
"""

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TK JSON Downloader — Dark Dante")
        self.setMinimumSize(1100, 720)

        self.settings = QSettings("TK", "TKJsonDownloader")

        # State
        self.catalog: List[CatalogItem] = []
        self.filtered: List[CatalogItem] = []

        self.index_url = self.settings.value("index_url", INDEX_URL_DEFAULT)
        self.base_url  = self.settings.value("base_url", BASE_URL_DEFAULT)
        self.out_dir   = self.settings.value("out_dir", os.path.expanduser("~/Scaricati/TK_JSON"))
        self.also_preview = (str(self.settings.value("also_preview", "true")).lower() == "true")

        # UI
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("TK JSON Downloader")
        title.setObjectName("Title")
        subtitle = QLabel("Catalogo worlds + download selettivo (JSON + preview).")
        subtitle.setObjectName("Subtle")
        header_left = QVBoxLayout()
        header_left.addWidget(title)
        header_left.addWidget(subtitle)
        header.addLayout(header_left)
        header.addStretch(1)

        self.btn_refresh = QPushButton("Aggiorna catalogo")
        self.btn_refresh.clicked.connect(self.refresh_catalog)
        header.addWidget(self.btn_refresh)

        root_layout.addLayout(header)

        # Top controls panel
        panel = QFrame()
        panel.setObjectName("Panel")
        panel_layout = QHBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Cerca per nome, tag o descrizione…")
        self.search.textChanged.connect(self.apply_filter)

        self.tag_filter = QComboBox()
        self.tag_filter.addItem("(tutti i tag)")
        self.tag_filter.currentIndexChanged.connect(self.apply_filter)

        self.chk_preview = QCheckBox("Scarica anche preview")
        self.chk_preview.setChecked(self.also_preview)

        self.out_dir_lbl = QLabel(self.out_dir)
        self.out_dir_lbl.setObjectName("Subtle")
        self.out_dir_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.btn_pick_dir = QPushButton("Cartella…")
        self.btn_pick_dir.clicked.connect(self.pick_out_dir)

        panel_layout.addWidget(QLabel("Filtro:"))
        panel_layout.addWidget(self.search, 2)
        panel_layout.addWidget(QLabel("Tag:"))
        panel_layout.addWidget(self.tag_filter, 1)
        panel_layout.addWidget(self.chk_preview)
        panel_layout.addSpacing(10)
        panel_layout.addWidget(QLabel("Download:"))
        panel_layout.addWidget(self.out_dir_lbl, 2)
        panel_layout.addWidget(self.btn_pick_dir)

        root_layout.addWidget(panel)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter, 1)

        # Left: list
        left = QFrame()
        left.setObjectName("Panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)

        self.listw = QListWidget()
        self.listw.itemSelectionChanged.connect(self.on_select)
        self.listw.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

        left_layout.addWidget(QLabel("Worlds disponibili"))
        left_layout.addWidget(self.listw, 1)

        btn_row = QHBoxLayout()
        self.btn_download = QPushButton("Scarica selezionati")
        self.btn_download.clicked.connect(self.download_selected)
        self.btn_open_folder = QPushButton("Apri cartella download")
        self.btn_open_folder.clicked.connect(self.open_out_dir)

        btn_row.addWidget(self.btn_download)
        btn_row.addWidget(self.btn_open_folder)
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        # Right: preview
        right = QFrame()
        right.setObjectName("Panel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(10)

        self.preview_img = QLabel()
        self.preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_img.setMinimumHeight(320)
        self.preview_img.setStyleSheet("border: 1px solid #2a2a3b; border-radius: 12px; background: #0b0b10;")

        self.lbl_name = QLabel("—")
        self.lbl_name.setObjectName("Title")
        self.lbl_tags = QLabel("")
        self.lbl_tags.setObjectName("Subtle")
        self.lbl_urls = QLabel("")
        self.lbl_urls.setObjectName("Subtle")
        self.desc = QTextEdit()
        self.desc.setReadOnly(True)
        self.desc.setPlaceholderText("Descrizione…")

        self.progress = QProgressBar()
        self.progress.setValue(0)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)

        right_layout.addWidget(QLabel("Anteprima"))
        right_layout.addWidget(self.preview_img)
        right_layout.addWidget(self.lbl_name)
        right_layout.addWidget(self.lbl_tags)
        right_layout.addWidget(self.lbl_urls)
        right_layout.addWidget(QLabel("Descrizione"))
        right_layout.addWidget(self.desc, 1)
        right_layout.addWidget(self.progress)
        right_layout.addWidget(QLabel("Log"))
        right_layout.addWidget(self.log)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)

        # Menu (minimo)
        act_quit = QAction("Esci", self)
        act_quit.triggered.connect(self.close)
        m_file = self.menuBar().addMenu("File")
        m_file.addAction(act_quit)

        # Apply QSS
        self.setStyleSheet(DARK_DANTE_QSS)

        # Start
        self.refresh_catalog()


    # -----------------------------
    # Settings persistence
    # -----------------------------
    def closeEvent(self, event):
        self.settings.setValue("index_url", self.index_url)
        self.settings.setValue("base_url", self.base_url)
        self.settings.setValue("out_dir", self.out_dir)
        self.settings.setValue("also_preview", "true" if self.chk_preview.isChecked() else "false")
        super().closeEvent(event)

    # -----------------------------
    # Catalog fetch + filter
    # -----------------------------
    def refresh_catalog(self):
        self.btn_refresh.setEnabled(False)
        self.log_append(f"⟲ Leggo catalogo: {self.index_url}")

        self.fetch_worker = FetchCatalogWorker(self.index_url, self.base_url)
        self.fetch_worker.ok.connect(self.on_catalog_ok)
        self.fetch_worker.fail.connect(self.on_catalog_fail)
        self.fetch_worker.start()

    def on_catalog_ok(self, items: list):
        self.btn_refresh.setEnabled(True)
        self.catalog = items
        self.log_append(f"✓ Catalogo caricato: {len(self.catalog)} world")

        # popola tag filter
        tags = set()
        for it in self.catalog:
            for t in it.tags:
                tags.add(t)
        tags_sorted = sorted(tags, key=lambda s: s.lower())

        self.tag_filter.blockSignals(True)
        self.tag_filter.clear()
        self.tag_filter.addItem("(tutti i tag)")
        for t in tags_sorted:
            self.tag_filter.addItem(t)
        self.tag_filter.blockSignals(False)

        self.apply_filter()

    def on_catalog_fail(self, err: str):
        self.btn_refresh.setEnabled(True)
        self.log_append(f"✗ Errore catalogo: {err}")
        QMessageBox.critical(self, "Errore", f"Impossibile leggere il catalogo.\n\n{err}")

    def apply_filter(self):
        q = (self.search.text() or "").strip().lower()
        tag = self.tag_filter.currentText()
        if tag == "(tutti i tag)":
            tag = ""

        def match(it: CatalogItem) -> bool:
            if tag and tag not in it.tags:
                return False
            if not q:
                return True
            blob = " ".join([
                it.title or "",
                it.description or "",
                " ".join(it.tags),
                it.updated or ""
            ]).lower()
            return q in blob

        self.filtered = [it for it in self.catalog if match(it)]
        self.populate_list()

    def populate_list(self):
        self.listw.clear()
        for it in self.filtered:
            text = it.title
            if it.updated:
                text += f"   ·   {it.updated}"
            if it.tags:
                text += f"   ·   {', '.join(it.tags)}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, it)
            self.listw.addItem(item)

        if self.listw.count() > 0:
            self.listw.setCurrentRow(0)

    # -----------------------------
    # Selection / preview
    # -----------------------------
    def on_select(self):
        items = self.listw.selectedItems()
        if not items:
            return
        it: CatalogItem = items[0].data(Qt.ItemDataRole.UserRole)
        self.show_preview(it)

    def show_preview(self, it: CatalogItem):
        self.lbl_name.setText(it.title or "—")
        tags = ", ".join(it.tags) if it.tags else "—"
        self.lbl_tags.setText(f"Tags: {tags}")
        self.lbl_urls.setText(f"JSON: {it.json_url}\nPreview: {it.preview_url or '—'}")
        self.desc.setPlainText(it.description or "")

        # preview image (best-effort)
        if not it.preview_url:
            self.preview_img.setPixmap(QPixmap())
            self.preview_img.setText("Nessuna preview")
            return

        try:
            req = urllib.request.Request(it.preview_url, headers={"User-Agent": "TK-JSON-Downloader/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()

            pix = QPixmap()
            ok = pix.loadFromData(data)
            if ok:
                # fit nicely
                scaled = pix.scaled(
                    self.preview_img.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.preview_img.setPixmap(scaled)
                self.preview_img.setText("")
            else:
                self.preview_img.setPixmap(QPixmap())
                self.preview_img.setText("Preview non leggibile")
        except Exception:
            self.preview_img.setPixmap(QPixmap())
            self.preview_img.setText("Preview non disponibile")

    def resizeEvent(self, event):
        # re-scale current preview if present
        items = self.listw.selectedItems()
        if items:
            it: CatalogItem = items[0].data(Qt.ItemDataRole.UserRole)
            # se abbiamo un pixmap già settato, non ricarichiamo: lo ridimensioniamo
            pix = self.preview_img.pixmap()
            if pix is not None and not pix.isNull():
                scaled = pix.scaled(
                    self.preview_img.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.preview_img.setPixmap(scaled)
        super().resizeEvent(event)

    # -----------------------------
    # Download
    # -----------------------------
    def download_selected(self):
        sel = self.listw.selectedItems()
        if not sel:
            QMessageBox.information(self, "Info", "Seleziona almeno un world da scaricare.")
            return

        out_dir = self.out_dir
        if not out_dir or not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "Errore", f"Cartella download non valida.\n\n{e}")
                return

        items = [i.data(Qt.ItemDataRole.UserRole) for i in sel]
        also_preview = self.chk_preview.isChecked()

        self.progress.setValue(0)
        self.btn_download.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.log_append(f"⇣ Download avviato: {len(items)} elementi · preview={'ON' if also_preview else 'OFF'}")

        self.dl_worker = DownloadWorker(items, out_dir, also_preview)
        self.dl_worker.progress.connect(self.on_dl_progress)
        self.dl_worker.log.connect(self.log_append)
        self.dl_worker.done.connect(self.on_dl_done)
        self.dl_worker.fail.connect(self.on_dl_fail)
        self.dl_worker.start()

    def on_dl_progress(self, done: int, total: int, title: str):
        if total <= 0:
            return
        self.progress.setValue(int((done / total) * 100))
        self.statusBar().showMessage(f"Scaricato {done}/{total}: {title}")

    def on_dl_done(self):
        self.btn_download.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.progress.setValue(100)
        self.statusBar().showMessage("Download completato.")
        QMessageBox.information(self, "Fatto", "Download completato. 🖤")

    def on_dl_fail(self, err: str):
        self.btn_download.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.log_append(f"✗ Errore download: {err}")
        QMessageBox.critical(self, "Errore", f"Download fallito.\n\n{err}")

    # -----------------------------
    # Folder handling
    # -----------------------------
    def pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Scegli cartella download", self.out_dir)
        if d:
            self.out_dir = d
            self.out_dir_lbl.setText(self.out_dir)

    def open_out_dir(self):
        path = self.out_dir
        if not path:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # -----------------------------
    # Logging
    # -----------------------------
    def log_append(self, msg: str):
        self.log.append(msg)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
