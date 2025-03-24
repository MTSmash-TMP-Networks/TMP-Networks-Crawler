# TMP-Networks-Search 🔍🌐

**TMP-Networks-Search** ist eine leistungsstarke, eigenständige Suchmaschinenlösung mit lokalem Web-Crawling, Volltextsuche (FTS5), Video-/Bild-Erkennung, Offline-Snapshots und Peer-to-Peer-Aufgabenverteilung – entwickelt von der **TMP-SYSTEM-SERVICE GmbH**.

---

## 🚀 Features

- 🌍 **Webcrawler mit Tiefensuche** und Threading
- 📄 **FTS5-Volltextsuche** in Titeln, Beschreibungen, Keywords & Content
- 🖼️ **Bild- & Videoerkennung inkl. HLS, yt-dlp & HEVC-Support**
- 🔒 **Offline-Snapshots** (HTML & Nur-Text)
- 🔧 **Manueller und automatischer Crawl-Modus**
- 🤖 **Verteilte Task-Verarbeitung via REST API**
- 🖥️ **Übersichtliches Web-Interface (Flask-basiert)**
- 📦 **Lokale SQLite-Datenbank mit FTS & Triggern**

---

## 📸 Screenshots

<img width="1333" alt="Bildschirmfoto 2025-03-24 um 22 32 34" src="https://github.com/user-attachments/assets/fa91e5a6-ee5d-40f0-9a51-d8aa77a2912d" />


---

## 🛠️ Installation

### 🔧 Voraussetzungen

- Python 3.9+
- Google Chrome for Testing
- `chromedriver` (passend zur Chrome-Version)
- Optional: `yt-dlp`, `selenium`, `flask`, `beautifulsoup4` usw.

### 📦 Abhängigkeiten installieren

```bash
pip install -r requirements.txt
```


### 📁 Verzeichnisstruktur vorbereiten

```plaintext
project/
├── chrome/
│   └── chrome/
│       └── chrome.exe (Windows) oder Chrome.app/... (Mac/Linux)
├── drivers/
│   └── chromedriver[.exe]
├── templates/
│   └── *.html (werden beim ersten Start automatisch erzeugt)
├── web_index.db (wird automatisch erstellt)
└── main.py
```

---

## ▶️ Nutzung

### Starten der App

```bash
python main.py
```

Die Weboberfläche ist dann erreichbar unter:  
📍 http://localhost:7001

---

## 🌐 Funktionen im Überblick

| Funktion              | Beschreibung                                      |
|-----------------------|---------------------------------------------------|
| 🔎 Websuche            | Volltextsuche mit Relevanzbewertung (BM25)       |
| 🖼️ Bildersuche         | Suche in Alt-Text & URL                          |
| 🎞️ Videosuche          | HLS, mp4, yt-dlp & Blob Detection                |
| 🧠 Auto-Modus          | Crawler läuft automatisch mit Start-URL         |
| 🛠️ Manuelle Indexierung| Einzelne URL tiefer crawlen und indexieren      |
| 🔄 Offline-Modus       | Snapshot-Ansicht & Nur-Text-Modus                |
| 🤝 Peer-Netzwerk       | Aufgaben-Verteilung über mehrere Crawler         |

---

## 📡 API-Endpunkte

| Endpoint                     | Beschreibung                               |
|-----------------------------|--------------------------------------------|
| `/create_task`              | Externe Task erstellen                     |
| `/get_task_results/<id>`    | Ergebnisse eines Tasks abfragen            |
| `/api/pages`                | Alle indexierten Seiten abrufen            |
| `/api/search?q=...`         | API-basierte Suche                         |

---

## 👨‍💻 Entwicklerinfo

**Autor:** [Marek Templin / TMP-SYSTEM-SERVICE GmbH](https://www.tmp-networks.de)  
📧 Kontakt: info@tmp-networks.de

---

## ⚠️ Hinweise

- Dieses Projekt ist **kein Ersatz für Google**, sondern eine **Forschungslösung und eigenständige Suchmaschine**.
- **Achtung bei Robots.txt**: Im Auto-Modus kann sie ignoriert werden – bitte mit Bedacht einsetzen.
- `yt-dlp` wird verwendet, um eingebettete Videos zu extrahieren. Bitte respektiere Urheberrechte.

---

## 📄 Lizenz

MIT License – siehe [`LICENSE`](LICENSE)

---

## 🌟 Wenn dir das Projekt gefällt...

Gib dem Repository ein ⭐ auf GitHub oder teile es weiter!
