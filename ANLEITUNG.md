# Blattwerk PDF

PDF-Betrachter und -Editor für **macOS und Windows**.

## Starten

| | Mac | Windows |
|---|---|---|
| Fertige App | `dist/Blattwerk PDF.app` doppelklicken (in „Programme“ ziehen) | – (unten selbst bauen) |
| Direkt aus dem Quellcode | `Start Mac.command` doppelklicken | `Start Windows.bat` doppelklicken |
| Eigene App bauen | `App bauen Mac.command` | `App bauen Windows.bat` → `dist\Blattwerk PDF\Blattwerk PDF.exe` |

Voraussetzung für Skripte: Python 3.9 oder neuer (Windows: https://www.python.org, beim Installieren „Add to PATH“ anhaken).
Beim ersten Start werden PySide6 und PyMuPDF automatisch installiert.

> Mac: Beim ersten Öffnen der App ggf. **Rechtsklick → Öffnen**, da sie nicht von Apple signiert ist.

## Funktionen

- **Ansehen:** Zoom (Strg/Cmd + Mausrad, Trackpad-Pinch), Seitenbreite/Ganze Seite, Seitenleiste, Suche (Strg/Cmd+F, F3), Drucken
- **Kommentieren:** Markieren, Unterstreichen, Durchstreichen, Notizen, Freihand, Rechteck, Ellipse, Linie, Pfeil
- **Bearbeiten:** neues Textfeld, vorhandenen Text ändern, Bereich abdecken (weiß), schwärzen (Inhalt wird entfernt), Bild einfügen, Unterschrift zeichnen & platzieren, Formulare ausfüllen
- **Seiten:** drehen, löschen, leere Seite, PDF/Bild einfügen, PDFs zusammenführen, per Drag & Drop umsortieren, Seiten als neue PDF speichern, Seite als Bild exportieren
- Rückgängig/Wiederholen, Rechtsklick auf Anmerkungen (Text/Farbe ändern, löschen), Radierer, zuletzt geöffnete Dateien
- Öffnet auch Bilder (PNG/JPG/TIFF), XPS und EPUB und wandelt sie in PDF um

## Hinweise

- „Text bearbeiten“ ersetzt den angeklickten Textabschnitt. Ist die Originalschrift nicht verfügbar, wird eine ähnliche Standardschrift (Helvetica/Times/Courier) verwendet.
- Passwortgeschützte PDFs werden nach dem Entsperren **ohne** Passwort gespeichert.
- Lizenz: PyMuPDF steht unter AGPL – für private Nutzung problemlos; bei kommerziellem Weitervertrieb Lizenz beachten.
