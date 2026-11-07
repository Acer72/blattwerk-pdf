#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Blattwerk PDF – ein plattformübergreifender PDF-Betrachter und -Editor (macOS & Windows).

Funktionen:
  * PDFs (und Bilder) öffnen, anzeigen, zoomen, durchsuchen, drucken
  * Text markieren, unterstreichen, durchstreichen, kopieren
  * Text hinzufügen, vorhandenen Text bearbeiten, Notizen, Freihand, Formen, Pfeile
  * Bereiche abdecken (weiß) oder schwärzen (Inhalt wird entfernt)
  * Bilder und eine gezeichnete Unterschrift einfügen
  * Formularfelder ausfüllen
  * Seiten drehen, löschen, einfügen, verschieben (Drag & Drop), extrahieren, PDFs zusammenführen
  * Rückgängig / Wiederholen

Benötigt: Python 3.9+, PySide6, PyMuPDF
"""

import os
import sys
import math
import bisect
from collections import deque

import fitz  # PyMuPDF

from PySide6.QtCore import (Qt, QRectF, QPointF, QTimer, QSize, Signal, QEvent,
                            QSettings, QBuffer, QByteArray, QIODevice)
from PySide6.QtGui import (QAction, QActionGroup, QImage, QPixmap, QPainter, QColor,
                           QPen, QBrush, QKeySequence, QPainterPath, QIcon, QTransform,
                           QCursor)
from PySide6.QtWidgets import (QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
                               QGraphicsItem, QGraphicsRectItem, QGraphicsEllipseItem,
                               QGraphicsLineItem, QGraphicsPathItem, QListWidget,
                               QListWidgetItem, QDockWidget, QToolBar, QFileDialog,
                               QMessageBox, QInputDialog, QLabel, QLineEdit, QSpinBox,
                               QDoubleSpinBox, QComboBox, QColorDialog, QToolButton,
                               QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                               QPlainTextEdit, QDialogButtonBox, QWidget, QMenu,
                               QAbstractItemView, QStatusBar, QSizePolicy, QFormLayout)
from PySide6.QtPrintSupport import QPrinter, QPrintDialog

APP_NAME = "Blattwerk PDF"
PAGE_GAP = 14.0
MAX_UNDO = 25

TOOLS = [
    # key, Beschriftung, Tooltip
    ("select", "↖ Auswählen", "Text auswählen/kopieren, Formularfelder ausfüllen, Rechtsklick für Optionen"),
    ("hand", "✋ Hand", "Dokument verschieben"),
    ("highlight", "▌Markieren", "Text farbig hervorheben"),
    ("underline", "U̲ Unterstreichen", "Text unterstreichen"),
    ("strike", "S̶ Durchstreichen", "Text durchstreichen"),
    ("text", "T Text", "Klicken, um ein neues Textfeld einzufügen"),
    ("edittext", "✎ Text bearbeiten", "Auf vorhandenen Text klicken, um ihn zu ändern"),
    ("note", "🗨 Notiz", "Kommentar/Notiz anheften"),
    ("ink", "✍ Freihand", "Freihand zeichnen"),
    ("rect", "▭ Rechteck", "Rechteck zeichnen"),
    ("ellipse", "◯ Ellipse", "Ellipse zeichnen"),
    ("line", "╱ Linie", "Linie zeichnen"),
    ("arrow", "➔ Pfeil", "Pfeil zeichnen"),
    ("whiteout", "▯ Abdecken", "Bereich weiß übermalen"),
    ("redact", "█ Schwärzen", "Bereich schwärzen – Inhalt wird dauerhaft entfernt"),
    ("image", "🖼 Bild", "Bereich aufziehen und Bild einfügen"),
    ("signature", "✒ Unterschrift", "Unterschrift zeichnen und per Klick platzieren"),
    ("eraser", "⌫ Radierer", "Auf eine Anmerkung klicken, um sie zu löschen"),
]

DEFAULT_COLORS = {
    "highlight": QColor(255, 230, 0), "underline": QColor(0, 90, 220),
    "strike": QColor(220, 0, 0), "text": QColor(0, 0, 0), "note": QColor(255, 200, 0),
    "ink": QColor(0, 60, 200), "rect": QColor(220, 0, 0), "ellipse": QColor(220, 0, 0),
    "line": QColor(220, 0, 0), "arrow": QColor(220, 0, 0), "signature": QColor(0, 0, 80),
}


def rgb(c):
    return (c.redF(), c.greenF(), c.blueF())


def int_to_rgb(v):
    return (((v >> 16) & 255) / 255.0, ((v >> 8) & 255) / 255.0, (v & 255) / 255.0)


def qrect_to_fitz(r):
    return fitz.Rect(r.left(), r.top(), r.right(), r.bottom())


def fitz_to_qrect(r):
    return QRectF(r.x0, r.y0, r.width, r.height)


# ----------------------------------------------------------------------------------------
# Seiten-Darstellung
# ----------------------------------------------------------------------------------------

class PageItem(QGraphicsItem):
    """Eine PDF-Seite in der Szene. Koordinaten = PDF-Punkte der (gedrehten) Anzeige."""

    def __init__(self, win, index, w, h):
        super().__init__()
        self.win = win
        self.index = index
        self.w = w
        self.h = h
        self.cache = None  # (scale, QImage)

    def boundingRect(self):
        return QRectF(0, 0, self.w, self.h)

    def invalidate(self):
        self.cache = None
        self.update()

    def paint(self, painter, option, widget=None):
        r = self.boundingRect()
        # Schatten
        painter.fillRect(r.translated(2, 3), QColor(0, 0, 0, 70))
        painter.fillRect(r, Qt.white)
        t = painter.worldTransform()
        scale = math.hypot(t.m11(), t.m12())
        dev = painter.device()
        dpr = dev.devicePixelRatioF() if hasattr(dev, "devicePixelRatioF") else 1.0
        s = max(0.05, scale * dpr)
        # Speicher begrenzen (max ~36 Mio. Pixel pro Seite)
        s = min(s, math.sqrt(36e6 / max(1.0, self.w * self.h)))
        s = round(s, 2)
        if self.cache is None or abs(self.cache[0] - s) > 0.01:
            img = self.win.render_page(self.index, s)
            if img is None:
                return
            self.cache = (s, img)
            self.win.note_cached(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(r, self.cache[1])


class PdfView(QGraphicsView):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(88, 92, 96))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setAcceptDrops(False)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_AcceptTouchEvents, False)

    # Zoom mit Strg/Cmd + Mausrad und Trackpad-Pinch
    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self.win.zoom_by(1.12 if d > 0 else 1 / 1.12)
            e.accept()
            return
        super().wheelEvent(e)

    def event(self, e):
        if e.type() == QEvent.NativeGesture:
            try:
                if e.gestureType() == Qt.ZoomNativeGesture:
                    self.win.zoom_by(1.0 + e.value())
                    return True
            except Exception:
                pass
        return super().event(e)

    def mousePressEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        if self.win.on_press(sp, e):
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        if self.win.on_move(sp, e):
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        if self.win.on_release(sp, e):
            e.accept()
            return
        super().mouseReleaseEvent(e)


class ThumbList(QListWidget):
    orderChanged = Signal()

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.TopToBottom)
        self.setWrapping(False)
        self.setMovement(QListWidget.Snap)
        self.setResizeMode(QListWidget.Adjust)
        self.setIconSize(QSize(130, 170))
        self.setSpacing(6)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setUniformItemSizes(True)
        self.setGridSize(QSize(155, 212))
        self.setMinimumWidth(175)

    def dropEvent(self, e):
        super().dropEvent(e)
        QTimer.singleShot(0, self.orderChanged.emit)


# ----------------------------------------------------------------------------------------
# Dialoge
# ----------------------------------------------------------------------------------------

class TextDialog(QDialog):
    def __init__(self, parent, title, text="", fontsize=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit(text)
        self.edit.setMinimumSize(380, 140)
        lay.addWidget(self.edit)
        self.size = None
        if fontsize is not None:
            form = QFormLayout()
            self.size = QDoubleSpinBox()
            self.size.setRange(4, 200)
            self.size.setValue(fontsize)
            form.addRow("Schriftgröße:", self.size)
            lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("OK")
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.edit.setFocus()

    def text(self):
        return self.edit.toPlainText()


class SignatureCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 200)
        self.strokes = []
        self.color = QColor(0, 0, 80)
        self.setCursor(Qt.CrossCursor)

    def mousePressEvent(self, e):
        self.strokes.append([e.position()])
        self.update()

    def mouseMoveEvent(self, e):
        if self.strokes and e.buttons() & Qt.LeftButton:
            self.strokes[-1].append(e.position())
            self.update()

    def clear(self):
        self.strokes = []
        self.update()

    def _path(self):
        path = QPainterPath()
        for st in self.strokes:
            if not st:
                continue
            path.moveTo(st[0])
            if len(st) == 1:
                path.lineTo(st[0] + QPointF(0.5, 0.5))
            for p in st[1:]:
                path.lineTo(p)
        return path

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.white)
        p.setPen(QPen(QColor(200, 200, 200), 1, Qt.DashLine))
        y = self.height() * 0.75
        p.drawLine(20, int(y), self.width() - 20, int(y))
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(self.color, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(self._path())

    def to_png(self):
        if not self.strokes:
            return None
        path = self._path()
        br = path.boundingRect().adjusted(-4, -4, 4, 4)
        scale = 3.0
        img = QImage(int(br.width() * scale), int(br.height() * scale), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(scale, scale)
        p.translate(-br.topLeft())
        p.setPen(QPen(self.color, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.end()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        return bytes(ba)


class SignatureDialog(QDialog):
    def __init__(self, parent, color):
        super().__init__(parent)
        self.setWindowTitle("Unterschrift zeichnen")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Unterschreiben Sie mit der Maus oder dem Trackpad im weißen Feld:"))
        self.canvas = SignatureCanvas()
        self.canvas.color = color
        lay.addWidget(self.canvas)
        row = QHBoxLayout()
        b_clear = QPushButton("Löschen")
        b_clear.clicked.connect(self.canvas.clear)
        b_ok = QPushButton("Übernehmen")
        b_ok.setDefault(True)
        b_ok.clicked.connect(self.accept)
        b_cancel = QPushButton("Abbrechen")
        b_cancel.clicked.connect(self.reject)
        row.addWidget(b_clear)
        row.addStretch(1)
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        lay.addLayout(row)


# ----------------------------------------------------------------------------------------
# Hauptfenster
# ----------------------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("Blattwerk", "BlattwerkPDF")
        self.doc = None
        self.path = None
        self.modified = False
        self.undo_stack = []
        self.redo_stack = []
        self.page_items = []
        self.page_tops = []
        self.cached = deque()
        self.words_cache = {}
        self.zoom = 1.0
        self.tool = "select"
        self.drag = None
        self.selection = []          # [(page_index, [word tuples])]
        self.sel_items = []
        self.search_term = ""
        self.search_hits = []        # [(page_index, fitz.Rect disp)]
        self.search_pos = -1
        self.search_items = []
        self.thumb_queue = []
        self.colors = dict(DEFAULT_COLORS)
        self.line_width = 2.0
        self.font_size = 12.0

        self.setWindowTitle(APP_NAME)
        self.resize(1280, 860)
        self.setAcceptDrops(True)

        self.view = PdfView(self)
        self.setCentralWidget(self.view)
        self.view.verticalScrollBar().valueChanged.connect(self.update_current_page)

        self.thumbs = ThumbList()
        self.thumbs.itemClicked.connect(self.on_thumb_clicked)
        self.thumbs.orderChanged.connect(self.on_thumb_reorder)
        self.thumbs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self.thumb_menu)
        self.dock = QDockWidget("Seiten", self)
        self.dock.setObjectName("thumbs")
        self.dock.setWidget(self.thumbs)
        self.dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock)

        self.thumb_timer = QTimer(self)
        self.thumb_timer.setInterval(0)
        self.thumb_timer.timeout.connect(self.render_thumb_batch)

        self.build_actions()
        self.build_menus()
        self.build_toolbars()
        self.setStatusBar(QStatusBar())
        self.set_tool("select")
        self.update_ui_state()
        self.show_welcome()

    # ------------------------------------------------------------------ UI-Aufbau
    def act(self, text, slot, shortcut=None, tip=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        return a

    def build_actions(self):
        self.a_open = self.act("Öffnen …", self.open_dialog, QKeySequence.Open)
        self.a_save = self.act("Speichern", self.save, QKeySequence.Save)
        self.a_save_as = self.act("Speichern unter …", self.save_as, QKeySequence.SaveAs)
        self.a_print = self.act("Drucken …", self.print_doc, QKeySequence.Print)
        self.a_close = self.act("Dokument schließen", self.close_doc, QKeySequence.Close)
        self.a_quit = self.act("Beenden", self.close, QKeySequence.Quit)
        self.a_export_png = self.act("Aktuelle Seite als Bild exportieren …", self.export_png)
        self.a_undo = self.act("Rückgängig", self.undo, QKeySequence.Undo)
        self.a_redo = self.act("Wiederholen", self.redo, [QKeySequence.Redo, "Ctrl+Y"])
        self.a_copy = self.act("Text kopieren", self.copy_selection, QKeySequence.Copy)
        self.a_select_all = self.act("Ganzen Seitentext auswählen", self.select_all_page, QKeySequence.SelectAll)
        self.a_find = self.act("Suchen", self.focus_search, QKeySequence.Find)
        self.a_find_next = self.act("Weitersuchen", lambda: self.search_step(1), [QKeySequence.FindNext, "F3"])
        self.a_find_prev = self.act("Rückwärts suchen", lambda: self.search_step(-1), [QKeySequence.FindPrevious, "Shift+F3"])
        self.a_zoom_in = self.act("Vergrößern", lambda: self.zoom_by(1.2), [QKeySequence.ZoomIn, "Ctrl+="])
        self.a_zoom_out = self.act("Verkleinern", lambda: self.zoom_by(1 / 1.2), QKeySequence.ZoomOut)
        self.a_fit_w = self.act("Seitenbreite", self.fit_width, "Ctrl+2")
        self.a_fit_p = self.act("Ganze Seite", self.fit_page, "Ctrl+0")
        self.a_actual = self.act("Originalgröße (100 %)", lambda: self.set_zoom(1.0), "Ctrl+1")
        self.a_thumbs = self.dock.toggleViewAction()
        self.a_thumbs.setText("Seitenleiste")
        self.a_thumbs.setShortcut("F4")
        self.a_prev = self.act("Vorherige Seite", lambda: self.goto_page(self.current_page() - 1), "PgUp")
        self.a_next = self.act("Nächste Seite", lambda: self.goto_page(self.current_page() + 1), "PgDown")
        self.a_rot_r = self.act("Rechts drehen ↻", lambda: self.rotate_pages(90), "Ctrl+R")
        self.a_rot_l = self.act("Links drehen ↺", lambda: self.rotate_pages(-90), "Ctrl+Shift+R")
        self.a_del_page = self.act("Seite(n) löschen", self.delete_pages)
        self.a_blank = self.act("Leere Seite einfügen", self.insert_blank)
        self.a_ins_pdf = self.act("PDF/Bild einfügen (nach aktueller Seite) …", self.insert_pdf)
        self.a_append = self.act("PDFs anhängen / zusammenführen …", self.append_pdfs)
        self.a_extract = self.act("Ausgewählte Seiten als neue PDF speichern …", self.extract_pages)
        self.a_up = self.act("Seite nach vorne", lambda: self.move_page(-1))
        self.a_down = self.act("Seite nach hinten", lambda: self.move_page(1))
        self.a_new_sig = self.act("Unterschrift neu zeichnen …", self.new_signature)
        self.a_about = self.act("Über Blattwerk PDF", self.about)

        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for key, label, tip in TOOLS:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setToolTip(tip)
            a.setStatusTip(tip)
            a.triggered.connect(lambda checked=False, k=key: self.set_tool(k))
            self.tool_group.addAction(a)
            self.tool_actions[key] = a

        self.doc_actions = [self.a_save, self.a_save_as, self.a_print, self.a_close, self.a_export_png,
                            self.a_copy, self.a_select_all, self.a_find, self.a_zoom_in, self.a_zoom_out,
                            self.a_fit_w, self.a_fit_p, self.a_actual, self.a_prev, self.a_next,
                            self.a_rot_r, self.a_rot_l, self.a_del_page, self.a_blank, self.a_ins_pdf,
                            self.a_append, self.a_extract, self.a_up, self.a_down]

    def build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Datei")
        m.addAction(self.a_open)
        self.recent_menu = m.addMenu("Zuletzt geöffnet")
        self.update_recent_menu()
        m.addSeparator()
        m.addActions([self.a_save, self.a_save_as])
        m.addSeparator()
        m.addAction(self.a_export_png)
        m.addAction(self.a_print)
        m.addSeparator()
        m.addAction(self.a_close)
        m.addAction(self.a_quit)

        m = mb.addMenu("&Bearbeiten")
        m.addActions([self.a_undo, self.a_redo])
        m.addSeparator()
        m.addActions([self.a_copy, self.a_select_all])
        m.addSeparator()
        m.addActions([self.a_find, self.a_find_next, self.a_find_prev])
        m.addSeparator()
        m.addAction(self.a_new_sig)

        m = mb.addMenu("&Ansicht")
        m.addActions([self.a_zoom_in, self.a_zoom_out, self.a_actual, self.a_fit_w, self.a_fit_p])
        m.addSeparator()
        m.addActions([self.a_prev, self.a_next])
        m.addSeparator()
        m.addAction(self.a_thumbs)

        m = mb.addMenu("&Seiten")
        m.addActions([self.a_rot_r, self.a_rot_l])
        m.addSeparator()
        m.addActions([self.a_up, self.a_down])
        m.addSeparator()
        m.addActions([self.a_blank, self.a_ins_pdf, self.a_append])
        m.addSeparator()
        m.addActions([self.a_extract, self.a_del_page])

        m = mb.addMenu("&Werkzeuge")
        for key, _, _ in TOOLS:
            m.addAction(self.tool_actions[key])

        m = mb.addMenu("&Hilfe")
        m.addAction(self.a_about)

    def build_toolbars(self):
        tb = QToolBar("Datei")
        tb.setObjectName("main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.TopToolBarArea, tb)
        tb.addAction(self.a_open)
        tb.addAction(self.a_save)
        tb.addAction(self.a_print)
        tb.addSeparator()
        tb.addAction(self.a_undo)
        tb.addAction(self.a_redo)
        tb.addSeparator()
        tb.addAction(self.a_prev)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(lambda v: self.goto_page(v - 1))
        tb.addWidget(self.page_spin)
        self.page_label = QLabel(" / 0 ")
        tb.addWidget(self.page_label)
        tb.addAction(self.a_next)
        tb.addSeparator()
        tb.addAction(self.a_zoom_out)
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        for z in ["50 %", "75 %", "100 %", "125 %", "150 %", "200 %", "300 %", "400 %"]:
            self.zoom_box.addItem(z)
        self.zoom_box.setMinimumWidth(90)
        self.zoom_box.activated.connect(self.on_zoom_box)
        self.zoom_box.lineEdit().returnPressed.connect(self.on_zoom_box)
        tb.addWidget(self.zoom_box)
        tb.addAction(self.a_zoom_in)
        tb.addAction(self.a_fit_w)
        tb.addAction(self.a_fit_p)
        tb.addSeparator()
        tb.addAction(self.a_rot_l)
        tb.addAction(self.a_rot_r)
        for a, t in ((self.a_prev, "▲"), (self.a_next, "▼"), (self.a_zoom_out, "−"), (self.a_zoom_in, "+"),
                     (self.a_rot_l, "↺"), (self.a_rot_r, "↻"), (self.a_undo, "↶ Rückgängig"),
                     (self.a_redo, "↷")):
            a.setIconText(t)
            a.setToolTip(a.text())

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Suchen …")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(200)
        self.search_edit.returnPressed.connect(self.on_search_enter)
        self.search_edit.textChanged.connect(self.on_search_text_changed)
        tb.addWidget(self.search_edit)
        tb.addAction(self.a_find_prev)
        tb.addAction(self.a_find_next)
        self.a_find_prev.setIconText("◀")
        self.a_find_next.setIconText("▶")
        self.search_label = QLabel("")
        tb.addWidget(self.search_label)

        tt = QToolBar("Werkzeuge")
        tt.setObjectName("tools")
        tt.setMovable(False)
        tt.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tt.setOrientation(Qt.Vertical)
        self.addToolBar(Qt.LeftToolBarArea, tt)
        tt.setStyleSheet("QToolButton{text-align:left;padding:4px 8px;}")
        for key, _, _ in TOOLS:
            tt.addAction(self.tool_actions[key])
        tt.addSeparator()
        self.color_btn = QToolButton()
        self.color_btn.setToolTip("Farbe des aktuellen Werkzeugs")
        self.color_btn.clicked.connect(self.choose_color)
        tt.addWidget(self.color_btn)
        tt.addWidget(QLabel("Linienstärke:"))
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.5, 20)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setValue(self.line_width)
        self.width_spin.valueChanged.connect(lambda v: setattr(self, "line_width", v))
        tt.addWidget(self.width_spin)
        tt.addWidget(QLabel("Schriftgröße:"))
        self.font_spin = QDoubleSpinBox()
        self.font_spin.setRange(4, 144)
        self.font_spin.setValue(self.font_size)
        self.font_spin.valueChanged.connect(lambda v: setattr(self, "font_size", v))
        tt.addWidget(self.font_spin)

    def update_color_button(self):
        c = self.colors.get(self.tool)
        if c is None:
            self.color_btn.setEnabled(False)
            self.color_btn.setText("  Farbe  ")
            self.color_btn.setStyleSheet("")
            return
        self.color_btn.setEnabled(True)
        self.color_btn.setText("  Farbe  ")
        fg = "black" if c.lightness() > 140 else "white"
        self.color_btn.setStyleSheet(
            "QToolButton{background:%s;color:%s;border:1px solid #888;border-radius:4px;padding:2px 6px;}"
            % (c.name(), fg))

    def choose_color(self):
        c = self.colors.get(self.tool)
        if c is None:
            return
        n = QColorDialog.getColor(c, self, "Farbe wählen")
        if n.isValid():
            self.colors[self.tool] = n
            self.update_color_button()

    def set_tool(self, key):
        self.tool = key
        self.tool_actions[key].setChecked(True)
        self.drag = None
        if key == "hand":
            self.view.setDragMode(QGraphicsView.ScrollHandDrag)
            self.view.viewport().setCursor(Qt.OpenHandCursor)
        else:
            self.view.setDragMode(QGraphicsView.NoDrag)
            if key in ("select", "highlight", "underline", "strike", "edittext"):
                self.view.viewport().setCursor(Qt.IBeamCursor)
            elif key in ("eraser", "note", "signature"):
                self.view.viewport().setCursor(Qt.PointingHandCursor)
            else:
                self.view.viewport().setCursor(Qt.CrossCursor)
        self.update_color_button()
        if key == "signature" and self.doc and not self.settings.value("signature"):
            self.new_signature()

    def update_ui_state(self):
        has = self.doc is not None
        for a in self.doc_actions:
            a.setEnabled(has)
        for a in self.tool_actions.values():
            a.setEnabled(has)
        self.a_undo.setEnabled(bool(self.undo_stack))
        self.a_redo.setEnabled(bool(self.redo_stack))
        self.page_spin.setEnabled(has)
        self.search_edit.setEnabled(has)
        self.zoom_box.setEnabled(has)
        name = os.path.basename(self.path) if self.path else ("Unbenannt" if has else "")
        title = APP_NAME if not has else "%s%s – %s" % (name, " •" if self.modified else "", APP_NAME)
        self.setWindowTitle(title)

    def show_welcome(self):
        sc = self.view.scene()
        sc.clear()
        t = sc.addText("PDF öffnen: Datei → Öffnen  (oder Datei hierher ziehen)")
        t.setDefaultTextColor(QColor(230, 230, 230))
        f = t.font()
        f.setPointSize(18)
        t.setFont(f)
        sc.setSceneRect(t.boundingRect().adjusted(-40, -200, 40, 200))

    # ------------------------------------------------------------------ Dateien
    def open_dialog(self):
        if not self.maybe_save():
            return
        start = self.settings.value("last_dir", os.path.expanduser("~"))
        f, _ = QFileDialog.getOpenFileName(
            self, "PDF öffnen", start,
            "PDF und Bilder (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.xps *.epub);;PDF (*.pdf);;Alle Dateien (*)")
        if f:
            self.open_file(f, ask=False)

    def open_file(self, path, ask=True):
        if ask and not self.maybe_save():
            return False
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            ext = os.path.splitext(path)[1].lower().lstrip(".") or "pdf"
            doc = fitz.open(stream=data, filetype=ext)
            if doc.needs_pass:
                while True:
                    pw, ok = QInputDialog.getText(self, "Passwort", "Dieses PDF ist geschützt. Passwort:",
                                                  QLineEdit.Password)
                    if not ok:
                        return False
                    if doc.authenticate(pw):
                        break
            if not doc.is_pdf:
                pdfbytes = doc.convert_to_pdf()
                doc = fitz.open(stream=pdfbytes, filetype="pdf")
                path_for_save = None
            else:
                path_for_save = path
        except Exception as ex:
            QMessageBox.critical(self, APP_NAME, "Die Datei konnte nicht geöffnet werden:\n%s" % ex)
            return False
        if doc.page_count == 0:
            QMessageBox.warning(self, APP_NAME, "Das Dokument enthält keine Seiten.")
            return False
        self.doc = doc
        self.path = path_for_save
        self.modified = path_for_save is None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.settings.setValue("last_dir", os.path.dirname(path))
        self.add_recent(path)
        self.clear_search()
        self.rebuild(keep_page=0)
        QTimer.singleShot(0, self.fit_width)
        self.update_ui_state()
        self.statusBar().showMessage("Geöffnet: %s (%d Seiten)" % (os.path.basename(path), doc.page_count), 5000)
        return True

    def add_recent(self, path):
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        rec = [p for p in rec if p != path]
        rec.insert(0, path)
        self.settings.setValue("recent", rec[:10])
        self.update_recent_menu()

    def update_recent_menu(self):
        self.recent_menu.clear()
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        rec = [p for p in rec if os.path.exists(p)]
        if not rec:
            a = self.recent_menu.addAction("(leer)")
            a.setEnabled(False)
        for p in rec:
            self.recent_menu.addAction(os.path.basename(p), lambda checked=False, x=p: self.open_file(x))

    def save(self):
        if not self.doc:
            return False
        if not self.path:
            return self.save_as()
        return self.write_to(self.path)

    def save_as(self):
        if not self.doc:
            return False
        start = self.path or os.path.join(self.settings.value("last_dir", os.path.expanduser("~")), "Dokument.pdf")
        f, _ = QFileDialog.getSaveFileName(self, "Speichern unter", start, "PDF (*.pdf)")
        if not f:
            return False
        if not f.lower().endswith(".pdf"):
            f += ".pdf"
        if self.write_to(f):
            self.path = f
            self.add_recent(f)
            self.update_ui_state()
            return True
        return False

    def write_to(self, path):
        try:
            data = self.doc.tobytes(garbage=3, deflate=True)
            tmp = path + ".blattwerk-tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except Exception as ex:
            QMessageBox.critical(self, APP_NAME, "Speichern fehlgeschlagen:\n%s" % ex)
            return False
        self.modified = False
        self.update_ui_state()
        self.statusBar().showMessage("Gespeichert: %s" % path, 5000)
        return True

    def maybe_save(self):
        if not self.doc or not self.modified:
            return True
        r = QMessageBox.question(self, APP_NAME, "Änderungen am Dokument speichern?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                                 QMessageBox.Save)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def close_doc(self):
        if not self.maybe_save():
            return
        self.doc = None
        self.path = None
        self.modified = False
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.page_items = []
        self.thumbs.clear()
        self.clear_search()
        self.show_welcome()
        self.update_ui_state()

    def closeEvent(self, e):
        if self.maybe_save():
            e.accept()
        else:
            e.ignore()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.open_file(urls[0])

    def export_png(self):
        i = self.current_page()
        base = os.path.splitext(os.path.basename(self.path or "Dokument"))[0]
        f, _ = QFileDialog.getSaveFileName(self, "Seite als Bild exportieren",
                                           "%s_Seite_%d.png" % (base, i + 1), "PNG (*.png);;JPEG (*.jpg)")
        if f:
            pix = self.doc[i].get_pixmap(dpi=200)
            pix.save(f)

    def print_doc(self):
        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(os.path.basename(self.path or "Dokument"))
        dlg = QPrintDialog(printer, self)
        dlg.setMinMax(1, self.doc.page_count)
        dlg.setFromTo(1, self.doc.page_count)
        if dlg.exec() != QDialog.Accepted:
            return
        first, last = printer.fromPage(), printer.toPage()
        if first == 0:
            first, last = 1, self.doc.page_count
        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.warning(self, APP_NAME, "Drucker konnte nicht gestartet werden.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dpi = min(300, printer.resolution())
            for n, i in enumerate(range(first - 1, last)):
                if n:
                    printer.newPage()
                pix = self.doc[i].get_pixmap(dpi=dpi, alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                target = painter.viewport()
                sz = img.size()
                sz.scale(target.size(), Qt.KeepAspectRatio)
                painter.setViewport(target.x(), target.y(), sz.width(), sz.height())
                painter.setWindow(img.rect())
                painter.drawImage(0, 0, img)
                painter.setViewport(target)
                painter.setWindow(target)
        finally:
            painter.end()
            QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------------ Undo
    def checkpoint(self):
        try:
            self.undo_stack.append(self.doc.tobytes())
        except Exception:
            return
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.modified = True
        self.update_ui_state()

    def _load_bytes(self, data):
        keep = self.current_page()
        self.doc = fitz.open(stream=data, filetype="pdf")
        self.rebuild(keep_page=min(keep, self.doc.page_count - 1))

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.doc.tobytes())
        self._load_bytes(self.undo_stack.pop())
        self.modified = True
        self.update_ui_state()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.doc.tobytes())
        self._load_bytes(self.redo_stack.pop())
        self.modified = True
        self.update_ui_state()

    # ------------------------------------------------------------------ Darstellung
    def render_page(self, i, s):
        if not self.doc or i >= self.doc.page_count:
            return None
        try:
            pix = self.doc[i].get_pixmap(matrix=fitz.Matrix(s, s), alpha=False)
        except Exception:
            return None
        return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()

    def note_cached(self, item):
        if item in self.cached:
            self.cached.remove(item)
        self.cached.append(item)
        while len(self.cached) > 14:
            old = self.cached.popleft()
            old.cache = None

    def rebuild(self, keep_page=None):
        """Szene und Miniaturen nach strukturellen Änderungen neu aufbauen."""
        if keep_page is None:
            keep_page = self.current_page()
        sc = self.view.scene()
        sc.clear()
        self.page_items = []
        self.page_tops = []
        self.cached.clear()
        self.words_cache.clear()
        self.sel_items = []
        self.selection = []
        self.search_items = []
        y = PAGE_GAP
        maxw = 0
        for i in range(self.doc.page_count):
            r = self.doc[i].rect
            it = PageItem(self, i, r.width, r.height)
            it.setPos(-r.width / 2.0, y)
            sc.addItem(it)
            self.page_items.append(it)
            self.page_tops.append(y)
            y += r.height + PAGE_GAP
            maxw = max(maxw, r.width)
        sc.setSceneRect(-maxw / 2.0 - PAGE_GAP * 3, 0, maxw + PAGE_GAP * 6, y)
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, self.doc.page_count))
        self.page_spin.blockSignals(False)
        self.page_label.setText(" / %d " % self.doc.page_count)
        self.rebuild_thumbs()
        if self.search_term:
            self.run_search(self.search_term, keep_pos=True)
        QTimer.singleShot(0, lambda: self.goto_page(keep_page))

    def rebuild_thumbs(self):
        self.thumbs.clear()
        ph = QPixmap(130, 168)
        ph.fill(QColor(235, 235, 235))
        for i in range(self.doc.page_count):
            item = QListWidgetItem(QIcon(ph), str(i + 1))
            item.setData(Qt.UserRole, i)
            item.setTextAlignment(Qt.AlignHCenter)
            self.thumbs.addItem(item)
        self.thumb_queue = list(range(self.doc.page_count))
        self.thumb_timer.start()

    def render_thumb_batch(self):
        if not self.doc:
            self.thumb_timer.stop()
            return
        for _ in range(4):
            if not self.thumb_queue:
                self.thumb_timer.stop()
                return
            i = self.thumb_queue.pop(0)
            self.render_thumb(i)

    def render_thumb(self, i):
        if i >= self.doc.page_count or i >= self.thumbs.count():
            return
        page = self.doc[i]
        r = page.rect
        s = min(260.0 / r.width, 340.0 / r.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(s, s), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(img)
        p = QPainter(pm)
        p.setPen(QPen(QColor(150, 150, 150), 2))
        p.drawRect(1, 1, pm.width() - 2, pm.height() - 2)
        p.end()
        self.thumbs.item(i).setIcon(QIcon(pm))

    def refresh_page(self, i):
        self.words_cache.pop(i, None)
        if 0 <= i < len(self.page_items):
            self.page_items[i].invalidate()
            self.render_thumb(i)

    def set_zoom(self, z):
        z = max(0.1, min(8.0, z))
        self.zoom = z
        self.view.setTransform(QTransform.fromScale(z, z))
        self.zoom_box.setEditText("%d %%" % round(z * 100))

    def zoom_by(self, f):
        if self.doc:
            self.set_zoom(self.zoom * f)

    def on_zoom_box(self, *args):
        t = self.zoom_box.currentText().replace("%", "").strip().replace(",", ".")
        try:
            self.set_zoom(float(t) / 100.0)
        except ValueError:
            self.set_zoom(self.zoom)

    def fit_width(self):
        if not self.page_items:
            return
        i = self.current_page()
        w = self.page_items[i].w
        avail = self.view.viewport().width() - 40
        self.set_zoom(avail / w)
        self.goto_page(i)

    def fit_page(self):
        if not self.page_items:
            return
        i = self.current_page()
        it = self.page_items[i]
        vw = self.view.viewport().width() - 40
        vh = self.view.viewport().height() - 2 * PAGE_GAP
        self.set_zoom(min(vw / it.w, vh / it.h))
        self.goto_page(i)

    def current_page(self):
        if not self.page_items:
            return 0
        top = self.view.mapToScene(0, 0).y()
        h = self.view.viewport().height() / max(self.zoom, 0.01)
        y = top + h * 0.35
        i = bisect.bisect_right(self.page_tops, y) - 1
        return max(0, min(i, len(self.page_items) - 1))

    def goto_page(self, i):
        if not self.page_items:
            return
        i = max(0, min(i, len(self.page_items) - 1))
        it = self.page_items[i]
        vh = self.view.viewport().height() / self.zoom
        cx = self.view.mapToScene(self.view.viewport().rect().center()).x()
        self.view.centerOn(cx, it.pos().y() - PAGE_GAP / 2 + vh / 2)
        self.update_current_page()

    def update_current_page(self, *args):
        if not self.page_items:
            return
        i = self.current_page()
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)
        if i < self.thumbs.count():
            item = self.thumbs.item(i)
            if not item.isSelected() or len(self.thumbs.selectedItems()) <= 1:
                self.thumbs.blockSignals(True)
                self.thumbs.setCurrentItem(item)
                self.thumbs.blockSignals(False)
                self.thumbs.scrollToItem(item)

    def on_thumb_clicked(self, item):
        if len(self.thumbs.selectedItems()) <= 1:
            self.goto_page(self.thumbs.row(item))

    def selected_pages(self):
        rows = sorted(self.thumbs.row(it) for it in self.thumbs.selectedItems())
        return rows or [self.current_page()]

    # ------------------------------------------------------------------ Koordinaten
    def page_at(self, sp):
        if not self.page_items:
            return None
        i = bisect.bisect_right(self.page_tops, sp.y()) - 1
        if 0 <= i < len(self.page_items):
            it = self.page_items[i]
            if it.sceneBoundingRect().contains(sp):
                return it
        return None

    @staticmethod
    def to_unrot_rect(page, qr):
        return (qrect_to_fitz(qr) * page.derotation_matrix).normalize()

    @staticmethod
    def to_unrot_pt(page, qp):
        return fitz.Point(qp.x(), qp.y()) * page.derotation_matrix

    @staticmethod
    def to_disp_rect(page, r):
        return fitz_to_qrect((fitz.Rect(r) * page.rotation_matrix).normalize())

    # ------------------------------------------------------------------ Textauswahl
    def get_words(self, i):
        if i not in self.words_cache:
            page = self.doc[i]
            out = []
            for w in page.get_text("words"):
                out.append((self.to_disp_rect(page, w[:4]), w[4], w[5], w[6], fitz.Rect(w[:4])))
            self.words_cache[i] = out
        return self.words_cache[i]

    def word_index_at(self, i, lp):
        words = self.get_words(i)
        if not words:
            return None
        best, bestd = None, 1e18
        for n, w in enumerate(words):
            r = w[0]
            if r.contains(lp):
                return n
            dx = max(r.left() - lp.x(), 0, lp.x() - r.right())
            dy = max(r.top() - lp.y(), 0, lp.y() - r.bottom())
            d = dx * dx + dy * dy * 4
            if d < bestd:
                best, bestd = n, d
        return best

    def clear_selection(self):
        for it in self.sel_items:
            if it.scene():
                it.scene().removeItem(it)
        self.sel_items = []
        self.selection = []

    def set_selection(self, i, a, b):
        self.clear_selection()
        if a is None or b is None:
            return
        if a > b:
            a, b = b, a
        words = self.get_words(i)[a:b + 1]
        self.selection = [(i, words)]
        parent = self.page_items[i]
        for r in self.line_rects(words, disp=True):
            ri = QGraphicsRectItem(r, parent)
            ri.setBrush(QColor(0, 120, 255, 70))
            ri.setPen(Qt.NoPen)
            self.sel_items.append(ri)

    @staticmethod
    def line_rects(words, disp=True):
        groups = {}
        order = []
        for w in words:
            k = (w[2], w[3])
            if k not in groups:
                groups[k] = None
                order.append(k)
            r = w[0] if disp else w[4]
            if disp:
                groups[k] = r if groups[k] is None else groups[k].united(r)
            else:
                groups[k] = fitz.Rect(r) if groups[k] is None else groups[k] | r
        return [groups[k] for k in order]

    def selected_text(self):
        parts = []
        for i, words in self.selection:
            last = None
            line = []
            for w in words:
                k = (w[2], w[3])
                if last is not None and k != last:
                    parts.append(" ".join(line))
                    line = []
                line.append(w[1])
                last = k
            if line:
                parts.append(" ".join(line))
        return "\n".join(parts)

    def copy_selection(self):
        t = self.selected_text()
        if t:
            QApplication.clipboard().setText(t)
            self.statusBar().showMessage("Text kopiert (%d Zeichen)" % len(t), 3000)

    def select_all_page(self):
        if self.search_edit.hasFocus():
            self.search_edit.selectAll()
            return
        i = self.current_page()
        n = len(self.get_words(i))
        if n:
            self.set_selection(i, 0, n - 1)

    # ------------------------------------------------------------------ Maus-Werkzeuge
    def on_press(self, sp, ev):
        if not self.doc:
            return False
        it = self.page_at(sp)
        if ev.button() == Qt.RightButton:
            if it:
                self.page_context_menu(it, it.mapFromScene(sp), ev.globalPosition().toPoint())
                return True
            return False
        if ev.button() != Qt.LeftButton or it is None or self.tool == "hand":
            return False
        lp = it.mapFromScene(sp)
        i = it.index
        tool = self.tool
        self.drag = {"item": it, "start": lp, "tool": tool, "temp": None, "w0": None}

        if tool == "select":
            if self.try_form_field(i, lp):
                self.drag = None
                return True
            self.clear_selection()
            self.drag["w0"] = self.word_index_at(i, lp)
        elif tool in ("highlight", "underline", "strike"):
            self.clear_selection()
            self.drag["w0"] = self.word_index_at(i, lp)
            if self.drag["w0"] is None:
                self.drag["temp"] = self._temp_rect(it, lp)
        elif tool in ("rect", "whiteout", "redact", "image"):
            self.drag["temp"] = self._temp_rect(it, lp)
        elif tool == "ellipse":
            e = QGraphicsEllipseItem(QRectF(lp, lp), it)
            e.setPen(QPen(self.colors[tool], self.line_width))
            self.drag["temp"] = e
        elif tool in ("line", "arrow"):
            l = QGraphicsLineItem(lp.x(), lp.y(), lp.x(), lp.y(), it)
            l.setPen(QPen(self.colors[tool], self.line_width))
            self.drag["temp"] = l
        elif tool == "ink":
            path = QPainterPath(lp)
            pi = QGraphicsPathItem(path, it)
            pi.setPen(QPen(self.colors["ink"], self.line_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            self.drag["temp"] = pi
            self.drag["points"] = [lp]
            self.drag["path"] = path
        else:
            # Klick-Werkzeuge
            self.drag = None
            if tool == "text":
                self.add_text_box(i, lp)
            elif tool == "edittext":
                self.edit_existing_text(i, lp)
            elif tool == "note":
                self.add_note(i, lp)
            elif tool == "eraser":
                self.erase_at(i, lp)
            elif tool == "signature":
                self.place_signature(i, lp)
        return True

    def _temp_rect(self, it, lp):
        r = QGraphicsRectItem(QRectF(lp, lp), it)
        tool = self.tool
        if tool == "redact":
            r.setBrush(QColor(0, 0, 0, 120))
            r.setPen(QPen(Qt.black, 0))
        elif tool == "whiteout":
            r.setBrush(QColor(255, 255, 255, 220))
            r.setPen(QPen(QColor(120, 120, 120), 0, Qt.DashLine))
        elif tool == "image":
            r.setPen(QPen(QColor(0, 120, 255), 0, Qt.DashLine))
        elif tool in self.colors:
            c = QColor(self.colors[tool])
            if tool == "highlight":
                c.setAlpha(110)
                r.setBrush(c)
                r.setPen(Qt.NoPen)
            else:
                r.setPen(QPen(c, self.line_width))
        return r

    def on_move(self, sp, ev):
        d = self.drag
        if not d:
            return False
        it = d["item"]
        lp = it.mapFromScene(sp)
        lp = QPointF(min(max(lp.x(), 0), it.w), min(max(lp.y(), 0), it.h))
        tool = d["tool"]
        if tool in ("select", "highlight", "underline", "strike") and d["w0"] is not None:
            w1 = self.word_index_at(it.index, lp)
            self.set_selection(it.index, d["w0"], w1)
        elif isinstance(d["temp"], QGraphicsRectItem) or isinstance(d["temp"], QGraphicsEllipseItem):
            d["temp"].setRect(QRectF(d["start"], lp).normalized())
        elif isinstance(d["temp"], QGraphicsLineItem):
            s = d["start"]
            d["temp"].setLine(s.x(), s.y(), lp.x(), lp.y())
        elif tool == "ink":
            d["points"].append(lp)
            d["path"].lineTo(lp)
            d["temp"].setPath(d["path"])
        return True

    def on_release(self, sp, ev):
        d = self.drag
        if not d:
            return False
        self.drag = None
        it = d["item"]
        i = it.index
        lp = it.mapFromScene(sp)
        lp = QPointF(min(max(lp.x(), 0), it.w), min(max(lp.y(), 0), it.h))
        tool = d["tool"]
        temp = d["temp"]
        if temp is not None and temp.scene():
            temp.scene().removeItem(temp)
        rect = QRectF(d["start"], lp).normalized()
        big = rect.width() > 3 and rect.height() > 3

        if tool == "select":
            if self.selection and self.selected_text():
                self.statusBar().showMessage("Text ausgewählt – Strg/Cmd+C zum Kopieren", 3000)
            return True
        if tool in ("highlight", "underline", "strike"):
            if self.selection:
                words = self.selection[0][1]
                self.clear_selection()
                self.add_markup(i, tool, rects=self.line_rects(words, disp=False))
            elif big:
                page = self.doc[i]
                self.add_markup(i, tool, rects=[self.to_unrot_rect(page, rect)])
            return True
        if tool in ("rect", "ellipse") and big:
            self.add_shape(i, tool, rect)
        elif tool in ("line", "arrow"):
            if (lp - d["start"]).manhattanLength() > 3:
                self.add_line(i, d["start"], lp, arrow=(tool == "arrow"))
        elif tool == "ink":
            if len(d["points"]) > 1:
                self.add_ink(i, d["points"])
        elif tool == "whiteout" and big:
            self.whiteout(i, rect)
        elif tool == "redact" and big:
            self.redact(i, rect)
        elif tool == "image":
            if not big:
                rect = QRectF(lp, QSize(200, 150))
            self.insert_image(i, rect)
        return True

    # ------------------------------------------------------------------ Bearbeitungen
    def add_markup(self, i, kind, rects):
        if not rects:
            return
        self.checkpoint()
        page = self.doc[i]
        if kind == "highlight":
            a = page.add_highlight_annot(rects)
        elif kind == "underline":
            a = page.add_underline_annot(rects)
        else:
            a = page.add_strikeout_annot(rects)
        if a:
            a.set_colors(stroke=rgb(self.colors[kind]))
            a.update()
        self.refresh_page(i)

    def add_shape(self, i, kind, rect):
        self.checkpoint()
        page = self.doc[i]
        r = self.to_unrot_rect(page, rect)
        a = page.add_rect_annot(r) if kind == "rect" else page.add_circle_annot(r)
        a.set_colors(stroke=rgb(self.colors[kind]))
        a.set_border(width=self.line_width)
        a.update()
        self.refresh_page(i)

    def add_line(self, i, p0, p1, arrow=False):
        self.checkpoint()
        page = self.doc[i]
        a = page.add_line_annot(self.to_unrot_pt(page, p0), self.to_unrot_pt(page, p1))
        c = rgb(self.colors["arrow" if arrow else "line"])
        a.set_colors(stroke=c, fill=c)
        a.set_border(width=self.line_width)
        if arrow:
            a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
        a.update()
        self.refresh_page(i)

    def add_ink(self, i, points):
        self.checkpoint()
        page = self.doc[i]
        pts = [tuple(self.to_unrot_pt(page, p)) for p in points]
        a = page.add_ink_annot([pts])
        a.set_colors(stroke=rgb(self.colors["ink"]))
        a.set_border(width=self.line_width)
        a.update()
        self.refresh_page(i)

    def add_text_box(self, i, lp):
        dlg = TextDialog(self, "Text einfügen", "", self.font_size)
        if dlg.exec() != QDialog.Accepted or not dlg.text().strip():
            return
        text = dlg.text()
        fs = dlg.size.value()
        self.font_size = fs
        self.font_spin.setValue(fs)
        lines = text.split("\n")
        w = max(fitz.get_text_length(l, fontname="helv", fontsize=fs) for l in lines) + fs * 0.9 + 6
        h = len(lines) * fs * 1.25 + fs * 0.6 + 4
        it = self.page_items[i]
        rect = QRectF(lp.x(), lp.y() - fs * 0.8, min(w, it.w - lp.x() + 1), h)
        self.checkpoint()
        page = self.doc[i]
        r = self.to_unrot_rect(page, rect)
        col = rgb(self.colors["text"])
        try:
            a = page.add_freetext_annot(r, text, fontsize=fs, fontname="helv", text_color=col,
                                        rotate=page.rotation)
        except TypeError:
            a = page.add_freetext_annot(r, text, fontsize=fs, fontname="helv", color=col,
                                        rotate=page.rotation)
        a.update()
        self.refresh_page(i)

    def edit_existing_text(self, i, lp):
        page = self.doc[i]
        up = self.to_unrot_pt(page, lp)
        found = None
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for line in b["lines"]:
                for sp in line["spans"]:
                    if fitz.Rect(sp["bbox"]).contains(up) and sp["text"].strip():
                        found = (sp, line)
                        break
                if found:
                    break
            if found:
                break
        if not found:
            self.statusBar().showMessage("An dieser Stelle wurde kein bearbeitbarer Text gefunden.", 4000)
            return
        sp, line = found
        dlg = TextDialog(self, "Text bearbeiten", sp["text"], sp["size"])
        dlg.edit.setMinimumSize(380, 80)
        if dlg.exec() != QDialog.Accepted:
            return
        new = dlg.text().replace("\n", " ")
        fs = dlg.size.value()
        if new == sp["text"] and abs(fs - sp["size"]) < 0.01:
            return
        self.checkpoint()
        bbox = fitz.Rect(sp["bbox"])
        shrink = bbox.height * 0.18
        rr = fitz.Rect(bbox.x0, bbox.y0 + shrink, bbox.x1, bbox.y1 - shrink)
        page.add_redact_annot(rr, fill=False)
        try:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        except (TypeError, AttributeError):
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        fname = sp.get("font", "").lower()
        flags = sp.get("flags", 0)
        bold = "bold" in fname or "black" in fname or bool(flags & 16)
        italic = "italic" in fname or "oblique" in fname or bool(flags & 2)
        if "courier" in fname or "mono" in fname or flags & 8:
            font = "cobo" if bold else "cour"
        elif "times" in fname or "serif" in fname and "sans" not in fname or flags & 4:
            font = {(False, False): "tiro", (True, False): "tibo", (False, True): "tiit", (True, True): "tibi"}[(bold, italic)]
        else:
            font = {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"}[(bold, italic)]
        if new.strip():
            d = line.get("dir", (1, 0))
            angle = int(round(math.degrees(math.atan2(-d[1], d[0])))) % 360
            rot = angle if angle in (0, 90, 180, 270) else 0
            page.insert_text(fitz.Point(sp["origin"]), new, fontsize=fs, fontname=font,
                             color=int_to_rgb(sp.get("color", 0)), rotate=rot)
        self.refresh_page(i)

    def add_note(self, i, lp):
        dlg = TextDialog(self, "Notiz", "")
        if dlg.exec() != QDialog.Accepted or not dlg.text().strip():
            return
        self.checkpoint()
        page = self.doc[i]
        a = page.add_text_annot(self.to_unrot_pt(page, lp), dlg.text(), icon="Note")
        a.set_colors(stroke=rgb(self.colors["note"]))
        a.update()
        self.refresh_page(i)

    def annot_at(self, i, lp):
        page = self.doc[i]
        up = self.to_unrot_pt(page, lp)
        hit = None
        for a in page.annots():
            r = fitz.Rect(a.rect)
            r = r + (-3, -3, 3, 3)
            if r.contains(up):
                hit = a  # letzte (oberste) gewinnt
        return page, hit

    def erase_at(self, i, lp):
        page, a = self.annot_at(i, lp)
        if a is None:
            self.statusBar().showMessage("Hier ist keine Anmerkung. (Eingefügter Text/Bilder: mit „Abdecken“ entfernen)", 4000)
            return
        xref = a.xref
        self.checkpoint()
        page = self.doc[i]
        for b in page.annots():
            if b.xref == xref:
                page.delete_annot(b)
                break
        self.refresh_page(i)

    def whiteout(self, i, rect):
        self.checkpoint()
        page = self.doc[i]
        page.draw_rect(self.to_unrot_rect(page, rect), color=None, fill=(1, 1, 1), overlay=True)
        self.refresh_page(i)

    def redact(self, i, rect):
        r = QMessageBox.question(self, "Schwärzen",
                                 "Der Inhalt in diesem Bereich (Text, Bilder, Grafiken) wird beim Speichern "
                                 "dauerhaft entfernt und schwarz überdeckt. Fortfahren?")
        if r != QMessageBox.Yes:
            return
        self.checkpoint()
        page = self.doc[i]
        page.add_redact_annot(self.to_unrot_rect(page, rect), fill=(0, 0, 0))
        page.apply_redactions()
        self.refresh_page(i)

    def insert_image(self, i, rect):
        start = self.settings.value("last_img_dir", os.path.expanduser("~"))
        f, _ = QFileDialog.getOpenFileName(self, "Bild einfügen", start,
                                           "Bilder (*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff)")
        if not f:
            return
        self.settings.setValue("last_img_dir", os.path.dirname(f))
        self.checkpoint()
        page = self.doc[i]
        try:
            page.insert_image(self.to_unrot_rect(page, rect), filename=f, keep_proportion=True,
                              rotate=page.rotation)
        except Exception as ex:
            self.undo_stack.pop()
            QMessageBox.warning(self, APP_NAME, "Bild konnte nicht eingefügt werden:\n%s" % ex)
            return
        self.refresh_page(i)

    def new_signature(self):
        dlg = SignatureDialog(self, self.colors["signature"])
        if dlg.exec() == QDialog.Accepted:
            png = dlg.canvas.to_png()
            if png:
                self.settings.setValue("signature", QByteArray(png))
                self.statusBar().showMessage("Unterschrift gespeichert – jetzt auf die Seite klicken, um sie zu platzieren.", 5000)
                return True
        return False

    def place_signature(self, i, lp):
        sig = self.settings.value("signature")
        if not sig:
            if not self.new_signature():
                return
            sig = self.settings.value("signature")
        png = bytes(sig)
        img = QImage.fromData(png)
        if img.isNull():
            return
        w = 160.0
        h = w * img.height() / max(1, img.width())
        it = self.page_items[i]
        rect = QRectF(lp.x() - w / 2, lp.y() - h / 2, w, h)
        rect = rect.intersected(QRectF(0, 0, it.w, it.h))
        self.checkpoint()
        page = self.doc[i]
        page.insert_image(self.to_unrot_rect(page, rect), stream=png, keep_proportion=True,
                          rotate=page.rotation)
        self.refresh_page(i)

    def try_form_field(self, i, lp):
        page = self.doc[i]
        up = self.to_unrot_pt(page, lp)
        for w in page.widgets():
            if not fitz.Rect(w.rect).contains(up):
                continue
            t = w.field_type
            name = w.field_label or w.field_name or "Feld"
            if t == fitz.PDF_WIDGET_TYPE_TEXT:
                cur = w.field_value or ""
                if w.field_flags & 4096:  # mehrzeilig
                    dlg = TextDialog(self, name, cur)
                    if dlg.exec() != QDialog.Accepted:
                        return True
                    val = dlg.text()
                else:
                    val, ok = QInputDialog.getText(self, "Formularfeld", name, QLineEdit.Normal, cur)
                    if not ok:
                        return True
                self.checkpoint()
                w.field_value = val
            elif t == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                self.checkpoint()
                on = w.on_state()
                w.field_value = (False if w.field_value not in (False, "Off", "", None) else (on or True))
            elif t == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                self.checkpoint()
                w.field_value = w.on_state() or True
            elif t in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
                vals = []
                for v in (w.choice_values or []):
                    vals.append(v[1] if isinstance(v, (list, tuple)) else v)
                if not vals:
                    return True
                cur = vals.index(w.field_value) if w.field_value in vals else 0
                val, ok = QInputDialog.getItem(self, "Auswahl", name, vals, cur, False)
                if not ok:
                    return True
                self.checkpoint()
                w.field_value = val
            else:
                return False
            w.update()
            self.refresh_page(i)
            return True
        return False

    # ------------------------------------------------------------------ Kontextmenüs
    def page_context_menu(self, it, lp, gpos):
        i = it.index
        page, a = self.annot_at(i, lp)
        m = QMenu(self)
        if self.selection and self.selected_text():
            m.addAction("Text kopieren", self.copy_selection)
            sel_words = self.selection[0][1]
            sel_page = self.selection[0][0]
            for kind, label in (("highlight", "Markieren"), ("underline", "Unterstreichen"), ("strike", "Durchstreichen")):
                m.addAction(label, lambda k=kind: (self.add_markup(sel_page, k, self.line_rects(sel_words, disp=False)),
                                                   self.clear_selection()))
            m.addSeparator()
        if a is not None:
            xref = a.xref
            atype = a.type[1]
            if atype in ("FreeText", "Text", "Highlight", "Underline", "StrikeOut", "Square", "Circle", "Ink", "Line"):
                m.addAction("Kommentar/Text bearbeiten …", lambda: self.edit_annot_text(i, xref))
            m.addAction("Farbe ändern …", lambda: self.change_annot_color(i, xref))
            m.addAction("Anmerkung löschen", lambda: self.delete_annot_xref(i, xref))
            m.addSeparator()
        m.addAction(self.a_rot_r)
        m.addAction(self.a_rot_l)
        m.addAction("Diese Seite löschen", lambda: self.delete_pages([i]))
        m.exec(gpos)

    def _annot_by_xref(self, page, xref):
        for a in page.annots():
            if a.xref == xref:
                return a
        return None

    def edit_annot_text(self, i, xref):
        page = self.doc[i]
        a = self._annot_by_xref(page, xref)
        if a is None:
            return
        dlg = TextDialog(self, "Text bearbeiten", a.info.get("content", ""))
        if dlg.exec() != QDialog.Accepted:
            return
        self.checkpoint()
        page = self.doc[i]
        a = self._annot_by_xref(page, xref)
        a.set_info(content=dlg.text())
        a.update()
        self.refresh_page(i)

    def change_annot_color(self, i, xref):
        c = QColorDialog.getColor(QColor(255, 0, 0), self, "Farbe wählen")
        if not c.isValid():
            return
        self.checkpoint()
        page = self.doc[i]
        a = self._annot_by_xref(page, xref)
        if a is None:
            return
        if a.type[1] == "FreeText":
            a.update(text_color=rgb(c))
        else:
            fill = rgb(c) if a.type[1] == "Line" else None
            a.set_colors(stroke=rgb(c), fill=fill)
            a.update()
        self.refresh_page(i)

    def delete_annot_xref(self, i, xref):
        self.checkpoint()
        page = self.doc[i]
        a = self._annot_by_xref(page, xref)
        if a is not None:
            page.delete_annot(a)
        self.refresh_page(i)

    def thumb_menu(self, pos):
        if not self.doc:
            return
        m = QMenu(self)
        m.addActions([self.a_rot_r, self.a_rot_l])
        m.addSeparator()
        m.addActions([self.a_up, self.a_down])
        m.addSeparator()
        m.addActions([self.a_blank, self.a_ins_pdf])
        m.addSeparator()
        m.addActions([self.a_extract, self.a_del_page])
        m.exec(self.thumbs.mapToGlobal(pos))

    # ------------------------------------------------------------------ Seitenoperationen
    def rotate_pages(self, delta):
        pages = self.selected_pages()
        self.checkpoint()
        for i in pages:
            p = self.doc[i]
            p.set_rotation((p.rotation + delta) % 360)
        self.rebuild(keep_page=pages[0])

    def delete_pages(self, pages=None):
        if pages is None or isinstance(pages, bool):
            pages = self.selected_pages()
        if len(pages) >= self.doc.page_count:
            QMessageBox.information(self, APP_NAME, "Es muss mindestens eine Seite übrig bleiben.")
            return
        txt = "Seite %d löschen?" % (pages[0] + 1) if len(pages) == 1 else "%d Seiten löschen?" % len(pages)
        if QMessageBox.question(self, APP_NAME, txt) != QMessageBox.Yes:
            return
        self.checkpoint()
        self.doc.delete_pages(pages)
        self.rebuild(keep_page=min(pages[0], self.doc.page_count - 1))

    def insert_blank(self):
        i = self.current_page()
        r = self.doc[i].rect
        self.checkpoint()
        self.doc.new_page(pno=i + 1, width=r.width, height=r.height)
        self.rebuild(keep_page=i + 1)

    def _open_other(self, f):
        ext = os.path.splitext(f)[1].lower().lstrip(".") or "pdf"
        with open(f, "rb") as fh:
            other = fitz.open(stream=fh.read(), filetype=ext)
        if other.needs_pass:
            pw, ok = QInputDialog.getText(self, "Passwort", "%s ist geschützt. Passwort:" % os.path.basename(f),
                                          QLineEdit.Password)
            if not ok or not other.authenticate(pw):
                raise RuntimeError("Falsches oder kein Passwort")
        if not other.is_pdf:
            other = fitz.open(stream=other.convert_to_pdf(), filetype="pdf")
        return other

    def insert_pdf(self):
        f, _ = QFileDialog.getOpenFileName(self, "PDF oder Bild einfügen",
                                           self.settings.value("last_dir", ""),
                                           "PDF und Bilder (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if not f:
            return
        i = self.current_page()
        try:
            other = self._open_other(f)
            self.checkpoint()
            self.doc.insert_pdf(other, start_at=i + 1)
        except Exception as ex:
            QMessageBox.warning(self, APP_NAME, "Einfügen fehlgeschlagen:\n%s" % ex)
            return
        self.rebuild(keep_page=i + 1)

    def append_pdfs(self):
        files, _ = QFileDialog.getOpenFileNames(self, "PDFs anhängen", self.settings.value("last_dir", ""),
                                                "PDF und Bilder (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if not files:
            return
        first_new = self.doc.page_count
        self.checkpoint()
        for f in files:
            try:
                self.doc.insert_pdf(self._open_other(f))
            except Exception as ex:
                QMessageBox.warning(self, APP_NAME, "%s konnte nicht angehängt werden:\n%s" % (os.path.basename(f), ex))
        self.rebuild(keep_page=first_new)

    def extract_pages(self):
        pages = self.selected_pages()
        base = os.path.splitext(os.path.basename(self.path or "Dokument"))[0]
        f, _ = QFileDialog.getSaveFileName(self, "Seiten speichern", "%s_Auszug.pdf" % base, "PDF (*.pdf)")
        if not f:
            return
        new = fitz.open()
        for i in pages:
            new.insert_pdf(self.doc, from_page=i, to_page=i)
        new.save(f, garbage=3, deflate=True)
        self.statusBar().showMessage("%d Seite(n) gespeichert: %s" % (len(pages), f), 5000)

    def move_page(self, delta):
        i = self.current_page()
        j = i + delta
        if j < 0 or j >= self.doc.page_count:
            return
        self.checkpoint()
        order = list(range(self.doc.page_count))
        order[i], order[j] = order[j], order[i]
        self.doc.select(order)
        self.rebuild(keep_page=j)

    def on_thumb_reorder(self):
        order = [self.thumbs.item(r).data(Qt.UserRole) for r in range(self.thumbs.count())]
        if order == list(range(len(order))) or sorted(order) != list(range(self.doc.page_count)):
            return
        cur = self.thumbs.currentRow()
        self.checkpoint()
        self.doc.select(order)
        self.rebuild(keep_page=max(0, cur))

    # ------------------------------------------------------------------ Suche
    def focus_search(self):
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def on_search_text_changed(self, t):
        if not t:
            self.clear_search()

    def on_search_enter(self):
        t = self.search_edit.text().strip()
        if not t or not self.doc:
            return
        if t != self.search_term:
            self.run_search(t)
        else:
            self.search_step(1)

    def clear_search(self):
        for it in self.search_items:
            if it.scene():
                it.scene().removeItem(it)
        self.search_items = []
        self.search_hits = []
        self.search_pos = -1
        self.search_term = ""
        if hasattr(self, "search_label"):
            self.search_label.setText("")

    def run_search(self, t, keep_pos=False):
        old_pos = self.search_pos
        for it in self.search_items:
            if it.scene():
                it.scene().removeItem(it)
        self.search_items = []
        self.search_hits = []
        self.search_term = t
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for i in range(self.doc.page_count):
                page = self.doc[i]
                for r in page.search_for(t):
                    qr = self.to_disp_rect(page, r)
                    self.search_hits.append((i, qr))
                    ri = QGraphicsRectItem(qr, self.page_items[i])
                    ri.setBrush(QColor(255, 200, 0, 90))
                    ri.setPen(Qt.NoPen)
                    self.search_items.append(ri)
        finally:
            QApplication.restoreOverrideCursor()
        if not self.search_hits:
            self.search_label.setText(" keine Treffer ")
            self.search_pos = -1
            return
        if keep_pos and 0 <= old_pos < len(self.search_hits):
            self.search_pos = old_pos
            self.show_hit(scroll=False)
        else:
            cur = self.current_page()
            self.search_pos = next((n for n, h in enumerate(self.search_hits) if h[0] >= cur), 0)
            self.show_hit()

    def search_step(self, d):
        if not self.search_hits:
            t = self.search_edit.text().strip()
            if t and t != self.search_term:
                self.run_search(t)
            return
        self.search_pos = (self.search_pos + d) % len(self.search_hits)
        self.show_hit()

    def show_hit(self, scroll=True):
        for n, it in enumerate(self.search_items):
            it.setBrush(QColor(255, 120, 0, 150) if n == self.search_pos else QColor(255, 200, 0, 90))
        self.search_label.setText(" %d / %d " % (self.search_pos + 1, len(self.search_hits)))
        if scroll:
            i, qr = self.search_hits[self.search_pos]
            sr = self.page_items[i].mapRectToScene(qr)
            self.view.ensureVisible(sr, 80, 120)
            self.update_current_page()

    # ------------------------------------------------------------------ Sonstiges
    def about(self):
        QMessageBox.about(self, APP_NAME,
                          "<b>%s</b><br>PDF-Betrachter und -Editor für macOS und Windows.<br><br>"
                          "Basiert auf Qt (PySide6) und MuPDF (PyMuPDF).<br><br>"
                          "Tipps:<ul>"
                          "<li>Strg/Cmd + Mausrad oder Trackpad-Pinch: Zoomen</li>"
                          "<li>Seiten in der Seitenleiste per Drag &amp; Drop umsortieren</li>"
                          "<li>Rechtsklick auf Anmerkungen: bearbeiten, Farbe, löschen</li>"
                          "<li>„Text bearbeiten“: auf vorhandenen Text klicken</li></ul>" % APP_NAME)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.clear_selection()
            if self.drag and self.drag.get("temp") is not None and self.drag["temp"].scene():
                self.drag["temp"].scene().removeItem(self.drag["temp"])
            self.drag = None
            self.set_tool("select")
            return
        if e.key() == Qt.Key_Home and self.doc:
            self.goto_page(0)
            return
        if e.key() == Qt.Key_End and self.doc:
            self.goto_page(self.doc.page_count - 1)
            return
        super().keyPressEvent(e)


class App(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.win = None
        self.pending = []

    def event(self, e):
        # macOS: Datei per Doppelklick / "Öffnen mit" übergeben
        if e.type() == QEvent.FileOpen:
            f = e.file()
            if self.win:
                self.win.open_file(f)
            else:
                self.pending.append(f)
            return True
        return super().event(e)


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = App(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Blattwerk")
    app.setStyle("Fusion") if sys.platform.startswith("win") else None
    win = MainWindow()
    app.win = win
    win.show()
    files = [a for a in sys.argv[1:] if os.path.isfile(a)] + app.pending
    if files:
        QTimer.singleShot(0, lambda: win.open_file(files[0], ask=False))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
