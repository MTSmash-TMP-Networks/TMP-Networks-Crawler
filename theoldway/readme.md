# 📌 Search Engine Projekt (Oldway) 

Eine einfache Suchmaschine, die lokal auf deinem Rechner läuft. Bei diesem Script muss Google Chrome vorinstalliert sein. Verbraucht weniger Ressourcen als die Standard-Version.

## 🚀 Installation  

### 1️⃣ Google Chrome installieren  
Stelle sicher, dass Google Chrome auf deinem System installiert ist:  

- **Linux:**  
  ```bash
  sudo apt-get install chromium-browser
  ```  
- **Mac:**  
  ```bash
  brew install --cask chromium
  ```  
- **Windows:**  
  [Google Chromium herunterladen]([https://www.google.com/chrome/](https://chromium.woolyss.com/)) und installieren.  

### 2️⃣ Python 3.11 installieren  
Falls Python 3.11 noch nicht installiert ist, lade es von [python.org](https://www.python.org/downloads/) herunter und installiere es.  

### 3️⃣ Virtuelle Umgebung einrichten  
Es wird empfohlen, eine virtuelle Umgebung zu verwenden:  

```bash
python3.11 -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
```  

### 4️⃣ Abhängigkeiten installieren  
Nach der Aktivierung der virtuellen Umgebung installiere die benötigten Pakete:  

```bash
pip install -r requirements.txt
```  

### 5️⃣ Anwendung starten  
Starte die Anwendung mit:  

```bash
python3.11 main.py
```  

### 6️⃣ Zugriff auf die Suchmaschine  
Öffne deinen Browser und rufe die folgende Adresse auf:  

```
http://127.0.0.1:7001
```  
