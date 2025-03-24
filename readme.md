# TMP-Networks-Search

TMP-Networks-Search ist ein fortschrittlicher Webcrawler mit integrierter Volltextsuche (FTS5), Video- und Medienindexierung sowie einem automatischen Crawling-Modus. Entwickelt von der TMP-SYSTEM-SERVICE GmbH, bietet dieses Projekt umfassende Funktionen zur Erstellung einer individuellen Suchmaschine mit flexiblen Indexierungsoptionen.

---

## 🚀 Funktionen

- **Automatisiertes und Manuelles Crawling:**
  - Manueller Modus für gezielte URL-Indexierung
  - Automatischer Modus für umfangreiche Domain-Indexierung

- **Volltextsuche (FTS5):**
  - Präzise und schnelle Suchergebnisse
  - Unterstützung verschiedener Suchfelder (Titel, Beschreibung, Keywords, Inhalt)

- **Medien- und Videoindexierung:**
  - Indexierung von Bildern und Videos (inkl. Unterstützung von HLS-Streams, HEVC/H.265)
  - Video-Informationsextraktion via yt-dlp

- **Offline Snapshot:**
  - Speicherung und Anzeige der HTML-Seiten als Offline-Version
  - Möglichkeit, reine Text-Versionen der gespeicherten Inhalte anzuzeigen

- **Peer-to-Peer Crawling:**
  - API-Integration zur Verteilung von Suchaufgaben auf externe Crawler

- **Performance-Optimierung:**
  - Paralleles Crawling mit Multithreading
  - Selenium für dynamisches Rendern von Inhalten

---

## 🛠️ Technologie-Stack

- **Backend:** Python, Flask
- **Datenbank:** SQLite (mit FTS5-Erweiterung)
- **Frontend:** HTML, CSS, JavaScript
- **Crawler:** Selenium, yt-dlp, Requests, BeautifulSoup

---

## 📦 Installation

### 1. Repository klonen
```bash
git clone https://github.com/dein-benutzername/TMP-Networks-Search.git
cd TMP-Networks-Search
```

### 2. Abhängigkeiten installieren
```bash
pip install -r requirements.txt
```

### 3. Chrome und Chromedriver bereitstellen
- Stelle sicher, dass Google Chrome und Chromedriver im Ordner `chrome/` und `drivers/` verfügbar sind.

### 4. Anwendung starten
```bash
python main.py
```
Die App läuft standardmäßig auf `http://0.0.0.0:7001`

---

## 🖥️ Benutzeroberfläche
- **Websuche:** Präzise Suchergebnisse anzeigen und filtern.
- **Bild- und Videosuche:** Dedizierte Seiten für Medieninhalte.
- **Crawler-Status:** Überblick über den aktuellen Status und die Warteschlange.
- **Automodus:** Bequeme Verwaltung und Aktivierung automatischer Crawling-Prozesse.

---

## 📜 Lizenz
Dieses Projekt steht unter der MIT-Lizenz - siehe [LICENSE](LICENSE) für weitere Details.

---

## 📞 Support
Für Fragen, Anregungen oder Feedback öffne gerne ein Issue oder kontaktiere uns direkt.

---

**Entwickelt von [TMP-SYSTEM-SERVICE GmbH](https://www.tmp-networks.de)**

