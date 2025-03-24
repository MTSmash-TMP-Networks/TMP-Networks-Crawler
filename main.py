import warnings
warnings.filterwarnings("ignore", message="resource_tracker: There appear to be", category=UserWarning)

import requests
import logging
import tempfile
import uuid
import sqlite3
import time
import shutil
import threading
import os
import queue
import math
import concurrent.futures
import json
import re

from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import yt_dlp
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

def resource_path(relative_path):
    """Ermittle den absoluten Pfad zu einer Ressource, auch wenn die App gebündelt ist."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
	
app = Flask(__name__, template_folder="templates")

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Flask-App initialisieren
app = Flask(__name__)
app.secret_key = "dein_geheimes_schluessel"

auto_ignore_robots = False

# --- Datenbankaufbau (SQLite) ---
conn = sqlite3.connect("web_index.db", check_same_thread=False)
with conn:
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            title TEXT,
            description TEXT,
            keywords TEXT,
            content TEXT,
            html_content TEXT,
            indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_url TEXT,
            media_url TEXT UNIQUE,
            alt TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_url TEXT,
            video_url TEXT UNIQUE,
            title TEXT,
            description TEXT
        )
    ''')
    cur.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
            title, description, keywords, content,
            content='pages',
            content_rowid='id'
        )
    ''')
    cur.execute('''
        CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages
        BEGIN
            INSERT INTO pages_fts(rowid, title, description, keywords, content)
            VALUES (new.id, new.title, new.description, new.keywords, new.content);
        END;
    ''')
    cur.execute('''
        CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages
        BEGIN
            UPDATE pages_fts
            SET title = new.title,
                description = new.description,
                keywords = new.keywords,
                content = new.content
            WHERE rowid = new.id;
        END;
    ''')
    cur.execute('''
        CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages
        BEGIN
            DELETE FROM pages_fts WHERE rowid = old.id;
        END;
    ''')
    conn.commit()

# Globale Variablen und Lock-Objekte
visited_urls = set()
visited_lock = threading.Lock()

processed_log = []
log_lock = threading.Lock()

robots_parsers = {}
robots_lock = threading.Lock()

# Requests-Session mit Retry-Strategie
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) \
    AppleWebKit/537.36 (KHTML, wie Gecko) Chrome/98.0.4758.102 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "close",
    "Upgrade-Insecure-Requests": "1"
})

retry_strategy = Retry(
    total=1,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS"],
    backoff_factor=1,
    respect_retry_after_header=True,
    raise_on_status=False
)

adapter = HTTPAdapter(
    max_retries=retry_strategy,
    pool_connections=1000,
    pool_maxsize=1000
)
session.mount("http://", adapter)
session.mount("https://", adapter)

def heartbeat():
    heartbeat_interval = 60  # Sekunden zwischen den Heartbeats
    registration_url = "https://www.tmp-networks.de/websearch/api.php?action=register"
    while True:
        crawler_data = {
            "crawler_name": "TMP_Networks_Crawler",
            "host": get_hostname(),
            "ip": "",
            "timestamp": time.time()
        }
        try:
            response = requests.post(registration_url, json=crawler_data, timeout=5)
            if response.ok:
                logging.info(f"Heartbeat gesendet: {response.text}")
            else:
                logging.error(f"Heartbeat fehlgeschlagen: {response.status_code} - {response.text}")
        except Exception as e:
            logging.error(f"Fehler beim Senden des Heartbeats: {e}")
        time.sleep(heartbeat_interval)


# --- Registrierung und Task/Peering-Funktionen ---
def get_hostname():
    try:
        return os.uname()[1]
    except AttributeError:
        return os.getenv('COMPUTERNAME', 'localhost')

def register_crawler():
    registration_url = "https://www.tmp-networks.de/websearch/api.php?action=register"
    crawler_data = {
        "crawler_name": "TMP_Networks_Crawler",
        "host": get_hostname(),
        "ip": "",
        "timestamp": time.time()
    }
    try:
        response = requests.post(registration_url, json=crawler_data)
        logging.info("Registrierung: " + response.text)
    except Exception as e:
        logging.error(f"Fehler bei der Registrierung: {e}")

def get_registered_crawlers():
    api_url = "https://www.tmp-networks.de/websearch/api.php?action=list"
    try:
        response = requests.get(api_url)
        if response.ok:
            return response.json()
    except Exception as e:
        logging.error(f"Fehler beim Abrufen registrierter Crawler: {e}")
    return []

# Wenn Aufgaben erstellt werden sollen, definiere hier den zentralen Peer.
# Beachte: Dieser Endpunkt wird von der Python-App zum Erstellen von Suchaufträgen genutzt.
PEER_SERVERS = [
    {
        "name": "TMP Networks API",
        "url": "https://www.tmp-networks.de/websearch/api.php?action=create_task"
    }
]

# --- Lokale Suchfunktion (für Worker/Crawler) ---
def perform_local_search(query, field, sort):
    """
    Sucht lokal in der FTS5-Tabelle pages_fts nach 'query'.
    - field: "all", "title", "description", "keywords", "content"
    - sort: "relevance" (BM25) oder "newest" (ORDER BY indexed_at DESC)
    """
    # Definieren Sie erlaubte Felder und Sortierungen
    ALLOWED_FIELDS = {"all", "title", "description", "keywords", "content"}
    ALLOWED_SORTS = {"relevance", "newest"}

    # Validierung der Eingaben
    if field not in ALLOWED_FIELDS:
        logging.warning(f"Ungültiges Suchfeld: {field}. Standard 'all' wird verwendet.")
        field = "all"

    if sort not in ALLOWED_SORTS:
        logging.warning(f"Ungültige Sortierung: {sort}. Standard 'relevance' wird verwendet.")
        sort = "relevance"

    # Funktion zur Erkennung von Sonderzeichen
    def contains_special_characters(s):
        # Definieren Sie, welche Zeichen als Sonderzeichen gelten
        return bool(re.search(r'[^a-zA-Z0-9\s]', s))

    # Formatieren des Suchbegriffs
    if contains_special_characters(query):
        # Setzen Sie den Suchbegriff in Anführungszeichen, um ihn als Phrase zu behandeln
        formatted_query = f'"{query}"'
    else:
        formatted_query = query

    cur = conn.cursor()
    
    # Basis-SELECT-Anweisung mit BM25-Ranking
    base_select = """
        SELECT
            p.id,
            p.url,
            p.title,
            p.description,
            p.keywords,
            p.content,
            p.html_content,
            p.indexed_at,
            bm25(pages_fts, 5.0, 2.0, 1.0, 1.0) AS rank
        FROM pages p
        JOIN pages_fts ON p.id = pages_fts.rowid
    """

    # Aufbau der WHERE-Klausel basierend auf dem Suchfeld
    if field == "all":
        where_clause = "WHERE pages_fts MATCH ?"
        match_value = formatted_query
    else:
        # Verwenden Sie FTS5-Syntax für spezifische Felder und setzen Sie den Suchbegriff in Anführungszeichen
        where_clause = "WHERE pages_fts MATCH ?"
        match_value = f"{field}:({formatted_query})"

    # Aufbau der ORDER BY-Klausel basierend auf der Sortierung
    if sort == "relevance":
        order_by_clause = "ORDER BY rank ASC"
    elif sort == "newest":
        order_by_clause = "ORDER BY p.indexed_at DESC"
    else:
        order_by_clause = "ORDER BY rank ASC"  # Fallback auf Relevanz

    # Finales SQL zusammenbauen
    sql = f"{base_select} {where_clause} {order_by_clause} LIMIT 50"

    logging.debug(f"Durchgeführte SQL-Abfrage: {sql} mit Wert: {match_value}")
    
    try:
        cur.execute(sql, (match_value,))
        results = cur.fetchall()
    except sqlite3.OperationalError as e:
        logging.error(f"SQLite-Fehler bei perform_local_search: {e}")
        return []

    # Ergebnisse in ein strukturiertes Array packen
    output = []
    for row in results:
        output.append({
            "id": row[0],
            "url": row[1],
            "title": row[2],
            "description": row[3],
            "keywords": row[4],
            "content": row[5],
            "html_content": row[6],
            "indexed_at": row[7],
            "rank": row[8],  # Optional: BM25-Score
        })

    return output

# --- Worker: Pollt die API nach Aufgaben und bearbeitet sie als lokaler Crawler ---
def poll_for_tasks():
    api_get_task = "https://www.tmp-networks.de/websearch/api.php?action=get_task"
    api_submit = "https://www.tmp-networks.de/websearch/api.php?action=submit_results"
    while True:
        try:
            response = requests.get(api_get_task, timeout=5)
            if response.ok:
                tasks = response.json()
                for task in tasks:
                    task_id = task.get("id")
                    query = task.get("query", "")
                    field = task.get("field", "all")
                    sort = task.get("sort", "relevance")
                    logging.info(f"Task erhalten: {task_id} – Suche: {query}")
                    results = perform_local_search(query, field, sort)
                    payload = {"task_id": task_id, "results": results}
                    submit_resp = requests.post(api_submit, json=payload, timeout=5)
                    logging.info(f"Ergebnisse für Task {task_id} gesendet: {submit_resp.text}")
            else:
                logging.error("Fehler beim Abrufen von Aufgaben")
        except Exception as e:
            logging.error(f"Fehler in poll_for_tasks: {e}")
        time.sleep(5)

# --- Funktionen für Crawling, Rendering, Speicherung, etc. ---
def get_rendered_html(url):
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36")
    chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    # Erstelle einen temporären Ordner für das Chrome-Profil
    temp_profile = tempfile.mkdtemp()

    # Setze dieses Profil als user-data-dir
    chrome_options.add_argument(f"--user-data-dir={temp_profile}")

    # Verwende den eingebundenen portablen Chrome-Browser
    chrome_options.binary_location = resource_path("chrome/chrome")

    driver_path = resource_path("drivers/chromedriver")
    service = Service(executable_path=driver_path)

    driver = webdriver.Chrome(service=service, options=chrome_options)
    try:
        driver.get(url)
        time.sleep(3)
        html = driver.page_source
        logs = driver.get_log("performance")
    finally:
        driver.quit()
        # Lösche das temporäre Chrome-Profil wieder
        shutil.rmtree(temp_profile, ignore_errors=True)

    return html, logs
	
def extract_video_url_from_logs(logs):
    for entry in logs:
        try:
            message = json.loads(entry["message"])
        except Exception:
            continue
        message_data = message.get("message", {})
        if message_data.get("method") == "Network.responseReceived":
            response = message_data.get("params", {}).get("response", {})
            mime_type = response.get("mimeType", "")
            url = response.get("url", "")
            if mime_type.startswith("video") or url.endswith(".m3u8"):
                logging.info(f"Extrahierte Video-URL aus Logs: {url}")
                return url
    return None

def can_crawl(url):
    domain = urlparse(url).netloc
    with robots_lock:
        if domain not in robots_parsers:
            rp = RobotFileParser()
            rp.set_url(f"https://{domain}/robots.txt")
            try:
                rp.read()
            except Exception as e:
                logging.warning(f"Robots.txt für {domain} konnte nicht geladen werden: {e}")
                rp = None
            robots_parsers[domain] = rp
        rp = robots_parsers.get(domain)
    if rp is None:
        return True
    return rp.can_fetch("*", url)

def extract_meta(soup):
    description = soup.find("meta", attrs={"name": "description"})
    keywords = soup.find("meta", attrs={"name": "keywords"})
    return (
        description["content"] if description and description.get("content") else "No Description",
        keywords["content"] if keywords and keywords.get("content") else "No Keywords"
    )

def save_media(page_url, soup):
    for img in soup.find_all("img", src=True):
        media_url = urljoin(page_url, img["src"])
        alt_text = img.get("alt", "")
        with visited_lock:
            cur = conn.cursor()
            cur.execute("INSERT OR IGNORE INTO media (page_url, media_url, alt) VALUES (?, ?, ?)", 
                        (page_url, media_url, alt_text))
            conn.commit()

def is_video_url(url):
    parsed = urlparse(url)
    path = parsed.path.lower()
    video_extensions = ['.m3u8', '.mp4', '.webm', '.ogg', '.hevc', '.h265']
    return any(path.endswith(ext) for ext in video_extensions)

def extract_video_info(video_url):
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'no_warnings': True,
        'format': 'best'
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            best_url = info.get('url', video_url)
            title = info.get('title', 'Embedded Video')
            description = info.get('description', 'Embedded Video')
            return best_url, title, description
    except Exception as e:
        logging.error(f"Error extracting video info from {video_url}: {e}")
        return video_url, "Embedded Video", "Embedded Video"

def save_videos(page_url, soup, logs):
    for video in soup.find_all("video"):
        sources = video.find_all("source")
        if sources:
            for source in sources:
                if source.get("src"):
                    video_url = urljoin(page_url, source["src"])
                    if video_url.startswith("blob:"):
                        if "youtube" in page_url.lower():
                            best_url, title, description = extract_video_info(page_url)
                        else:
                            real_url = extract_video_url_from_logs(logs)
                            if real_url:
                                best_url, title, description = extract_video_info(real_url)
                            else:
                                best_url, title, description = video_url, "Embedded Video (Blob)", "Embedded Video (Blob)"
                    else:
                        if ".m3u8" in video_url or is_video_url(video_url) or "youtube" in video_url.lower():
                            best_url, title, description = extract_video_info(video_url)
                        else:
                            best_url = video_url
                            title = video.get("title", "Embedded Video")
                            description = video.get("alt", "Embedded Video")
                    with visited_lock:
                        cur = conn.cursor()
                        cur.execute("""
                            INSERT OR IGNORE INTO videos (page_url, video_url, title, description)
                            VALUES (?, ?, ?, ?)
                        """, (page_url, best_url, title, description))
                        conn.commit()
        else:
            if video.get("src"):
                video_url = urljoin(page_url, video["src"])
                if video_url.startswith("blob:"):
                    if "youtube" in page_url.lower():
                        best_url, title, description = extract_video_info(page_url)
                    else:
                        real_url = extract_video_url_from_logs(logs)
                        if real_url:
                            best_url, title, description = extract_video_info(real_url)
                        else:
                            best_url, title, description = video_url, "Embedded Video (Blob)", "Embedded Video (Blob)"
                else:
                    if ".m3u8" in video_url or is_video_url(video_url) or "youtube" in video_url.lower():
                        best_url, title, description = extract_video_info(video_url)
                    else:
                        best_url = video_url
                        title = video.get("title", "Embedded Video")
                        description = video.get("alt", "Embedded Video")
                with visited_lock:
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT OR IGNORE INTO videos (page_url, video_url, title, description)
                        VALUES (?, ?, ?, ?)
                    """, (page_url, best_url, title, description))
                    conn.commit()

def is_valid_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ["http", "https"]

def process_url(url, ignore_robots=False):
    with visited_lock:
        if url in visited_urls:
            return []
        visited_urls.add(url)
    if not is_valid_url(url):
        logging.info(f"Ungültige URL: {url}")
        return []
    if not ignore_robots and not can_crawl(url):
        logging.info(f"Robots.txt verbietet: {url}")
        return []
    logging.info(f"Verarbeite URL: {url}")
    try:
        html, logs = get_rendered_html(url)
    except Exception as e:
        logging.error(f"Fehler beim Rendern von {url} mit Selenium: {e}")
        return []
    logging.info(f"Länge des rendernden Inhalts: {len(html)}")
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        logging.info(f"Gefundener Titel: {soup.title.string.strip()}")
    else:
        logging.info("Kein Titel gefunden!")
    title = soup.title.string.strip() if soup.title and soup.title.string else "No Title"
    description, keywords = extract_meta(soup)
    content = " ".join(p.get_text().strip() for p in soup.find_all("p"))
    with visited_lock:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pages (url, title, description, keywords, content, html_content)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                keywords=excluded.keywords,
                content=excluded.content,
                html_content=excluded.html_content,
                indexed_at=CURRENT_TIMESTAMP
        """, (url, title, description, keywords, content, html))
        conn.commit()
    save_media(url, soup)
    save_videos(url, soup, logs)
    with log_lock:
        if url not in processed_log:
            processed_log.append(url)
        if len(processed_log) > 10:
            processed_log[:] = processed_log[-10:]
    new_links = []
    for link in soup.find_all("a", href=True):
        next_url = urljoin(url, link["href"]).split('#')[0]
        if is_valid_url(next_url):
            with visited_lock:
                if next_url not in visited_urls:
                    new_links.append(next_url)
    return new_links

def manual_crawl(url, depth=2):
    if depth == 0:
        return
    new_links = process_url(url, ignore_robots=True)
    threads = []
    for link in new_links:
        thread = threading.Thread(target=manual_crawl, args=(link, depth - 1))
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()
    time.sleep(1)

# --- Auto-Modus ---
auto_mode_enabled = False
auto_mode_worker_thread = None
auto_queue = queue.Queue()
current_auto_url = None
auto_mode_lock = threading.Lock()
auto_mode_start_url = ""
MAX_WORKERS = 10

def auto_crawl_worker():
    global current_auto_url
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            with auto_mode_lock:
                if not auto_mode_enabled:
                    break
            try:
                url = auto_queue.get(timeout=3)
            except queue.Empty:
                continue
            with auto_mode_lock:
                current_auto_url = url
            future = executor.submit(process_url, url, auto_ignore_robots)
            try:
                new_links = future.result(timeout=15)
            except Exception as e:
                logging.error(f"Fehler bei der Verarbeitung von {url}: {e}")
                new_links = []
            for link in new_links:
                auto_queue.put(link)
            with auto_mode_lock:
                current_auto_url = None
            auto_queue.task_done()
            time.sleep(0.1)

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

# --- Asynchrone Abfrage externer Aufgaben in der Suche ---
def fetch_peer_results(peer_url, query, field, sort):
    try:
        resp = requests.get(peer_url, params={"q": query, "field": field, "sort": sort}, timeout=5)
        if resp.ok:
            return resp.json()
    except Exception as e:
        logging.error(f"Fehler beim Abruf von {peer_url}: {e}")
    return []

# --- Neue Route: Aufgabenerstellung (für externe Suche) ---
@app.route("/create_task", methods=["POST"])
def create_task():
    data = request.get_json()
    if not data or "query" not in data:
        return jsonify({"status": "error", "message": "Query missing"}), 400
    api_url = "https://www.tmp-networks.de/websearch/api.php?action=create_task"
    try:
        resp = requests.post(api_url, json=data, timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- Neue Route: Abrufen der Task-Ergebnisse (wird per AJAX vom Ladebildschirm genutzt) ---
@app.route("/get_task_results/<task_id>", methods=["GET"])
def get_task_results(task_id):
    api_url = f"https://www.tmp-networks.de/websearch/api.php?action=get_results&task_id={task_id}"
    try:
        resp = requests.get(api_url, timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- Neue Route: Ladebildschirm, der auf die Ergebnisse einer Task wartet ---
@app.route("/task/<task_id>")
def task_status(task_id):
    return render_template("task_status.html", task_id=task_id)

# --- Standard Routen ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/search_results")
def search_results_task():
    task_id = request.args.get("task_id", "")
    api_url = f"https://www.tmp-networks.de/websearch/api.php?action=get_results&task_id={task_id}"
    try:
        resp = requests.get(api_url, timeout=5)
        data = resp.json()
        if data.get("status") == "completed":
            results = data.get("results", [])
        else:
            results = []
    except Exception as e:
        logging.error(f"Fehler beim Abrufen der Task-Ergebnisse: {e}")
        results = []
    return render_template("search_results_task.html", task_id=task_id, results=results)

@app.route("/manual_index", methods=["GET", "POST"])
def manual_index_route():
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        if url and is_valid_url(url):
            threading.Thread(target=manual_crawl, args=(url, 2)).start()
            flash(f"Manuelle Indexierung von {url} gestartet!", "success")
        else:
            flash("Bitte eine gültige URL eingeben.", "error")
        return redirect(url_for("manual_index_route"))
    return render_template("manual_index.html")

@app.route("/crawler_status")
def crawler_status_route():
    with auto_mode_lock:
        current = current_auto_url
        queue_size = auto_queue.qsize()
    with log_lock:
        log = list(processed_log)
    db_file = "web_index.db"
    file_size_bytes = os.path.getsize(db_file) if os.path.exists(db_file) else 0
    file_size = format_size(file_size_bytes)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pages")
    count_pages = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM media")
    count_images = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM videos")
    count_videos = cur.fetchone()[0]
    registered_crawlers = get_registered_crawlers()
    return render_template("crawler_status.html",
                           current=current,
                           queue_size=queue_size,
                           log=log,
                           file_size=file_size,
                           count_pages=count_pages,
                           count_images=count_images,
                           count_videos=count_videos,
                           registered_crawlers=registered_crawlers)

@app.route("/auto_mode", methods=["GET", "POST"])
def auto_mode_route():
    global auto_mode_enabled, auto_mode_start_url, auto_ignore_robots, auto_queue
    if request.method == "POST":
        action = request.form.get("action")
        if action == "enable":
            start_url = request.form.get("start_url", "").strip()
            if not start_url:
                flash("Bitte eine Startdomain eingeben, um den Automodus zu aktivieren.", "error")
                return redirect(url_for("auto_mode_route"))
            auto_mode_start_url = start_url
            auto_ignore_robots = (request.form.get("ignore_robots") == "1")
            with visited_lock:
                visited_urls.clear()
            auto_queue = queue.Queue()
            auto_queue.put(auto_mode_start_url)
            with auto_mode_lock:
                auto_mode_enabled = True
            flash("Automodus aktiviert. Bitte drücke 'Start Auto Crawl', um den Crawl zu starten.", "success")
        elif action == "disable":
            with auto_mode_lock:
                auto_mode_enabled = False
                if request.form.get("clear_queue") == "1":
                    while not auto_queue.empty():
                        try:
                            auto_queue.get_nowait()
                        except Exception:
                            break
            flash("Automodus deaktiviert.", "success")
        return redirect(url_for("auto_mode_route"))
    with auto_mode_lock:
        status = auto_mode_enabled
    return render_template("auto_mode.html",
                           auto_mode_enabled=status,
                           start_domain=auto_mode_start_url,
                           auto_ignore_robots=auto_ignore_robots)

@app.route("/start_auto", methods=["POST"])
def start_auto():
    global auto_mode_worker_thread
    with auto_mode_lock:
        if auto_mode_enabled and (auto_mode_worker_thread is None or not auto_mode_worker_thread.is_alive()):
            auto_mode_worker_thread = threading.Thread(target=auto_crawl_worker, daemon=True)
            auto_mode_worker_thread.start()
            flash("Auto Crawl wurde gestartet.", "success")
        else:
            flash("Automodus ist entweder deaktiviert oder läuft bereits.", "info")
    return redirect(url_for("auto_mode_route"))

@app.route("/search", methods=["GET"])
def web_search():
    query = request.args.get("q", "")
    field = request.args.get("field", "all").lower()
    sort = request.args.get("sort", "relevance")
    selected_peers = request.args.getlist("peers")

    # Falls externe Peers ausgewählt wurden
    if selected_peers:
        task_payload = {"query": query, "field": field, "sort": sort}
        api_create = "https://www.tmp-networks.de/websearch/api.php?action=create_task"
        try:
            resp = requests.post(api_create, json=task_payload, timeout=5)
            data = resp.json()
            if data.get("status") == "success":
                task_id = data.get("task_id")
                return redirect(url_for("task_status", task_id=task_id))
            else:
                flash("Fehler beim Erstellen der Suchaufgabe.", "error")
        except Exception as e:
            flash(f"Fehler: {e}", "error")

    # Lokale Suche per perform_local_search
    results = []
    total_count = 0

    if query:
        results = perform_local_search(query, field, sort)
        total_count = len(results)

    total_pages = 1 if total_count < 50 else (total_count // 50 + 1)
    return render_template(
        "search_results.html",
        query=query,
        field=field,
        sort=sort,
        results=results,
        page=1,
        total_pages=total_pages,
        peer_servers=PEER_SERVERS,
        selected_peers=selected_peers
    )


@app.route("/search_images", methods=["GET"])
def search_images():
    query = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    limit = 50
    offset = (page - 1) * limit
    results = []
    total_count = 0
    if query:
        like_query = "%" + query + "%"
        cur = conn.cursor()
        count_sql = "SELECT COUNT(*) FROM media WHERE media_url LIKE ? OR alt LIKE ?"
        cur.execute(count_sql, (like_query, like_query))
        total_count = cur.fetchone()[0]
        sql = "SELECT * FROM media WHERE media_url LIKE ? OR alt LIKE ? LIMIT ? OFFSET ?"
        cur.execute(sql, (like_query, like_query, limit, offset))
        results = cur.fetchall()
    total_pages = math.ceil(total_count / limit) if total_count > 0 else 1
    return render_template("search_images.html",
                           query=query,
                           results=results,
                           page=page,
                           total_pages=total_pages)

@app.route("/search_videos", methods=["GET"])
def search_videos():
    query = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    limit = 50
    offset = (page - 1) * limit
    results = []
    total_count = 0
    if query:
        like_query = "%" + query + "%"
        cur = conn.cursor()
        count_sql = "SELECT COUNT(*) FROM videos WHERE title LIKE ? OR description LIKE ? OR video_url LIKE ?"
        cur.execute(count_sql, (like_query, like_query, like_query))
        total_count = cur.fetchone()[0]
        sql = "SELECT * FROM videos WHERE title LIKE ? OR description LIKE ? OR video_url LIKE ? LIMIT ? OFFSET ?"
        cur.execute(sql, (like_query, like_query, like_query, limit, offset))
        results = cur.fetchall()
    total_pages = math.ceil(total_count / limit) if total_count > 0 else 1
    return render_template("search_videos.html",
                           query=query,
                           results=results,
                           page=page,
                           total_pages=total_pages)

@app.route("/api/pages", methods=["GET"])
def get_pages():
    cur = conn.cursor()
    cur.execute("SELECT * FROM pages")
    pages = cur.fetchall()
    return jsonify(pages)

# --- Neue Route für Offline-Snapshot ---
@app.route("/offline/<int:page_id>")
def offline(page_id):
    cur = conn.cursor()
    cur.execute("SELECT html_content, url FROM pages WHERE id=?", (page_id,))
    row = cur.fetchone()
    if not row:
        return "Offline snapshot not available", 404
    html_content, original_url = row
    # Wenn "text" im Query-Parameter gesetzt ist, wird der sichtbare Text extrahiert und überflüssiger Whitespace entfernt.
    if request.args.get("text"):
        soup = BeautifulSoup(html_content, "html.parser")
        plain_text = soup.get_text(separator="\n")
        lines = plain_text.splitlines()
        cleaned_lines = [line.strip() for line in lines if line.strip()]
        plain_text = "\n".join(cleaned_lines)
        return render_template("offline_text.html", plain_text=plain_text, original_url=original_url)
    else:
        return render_template("offline.html", html_content=html_content, original_url=original_url)

@app.route("/api/search", methods=["GET"])
def api_search():
    query = request.args.get("q", "")
    field = request.args.get("field", "all").lower()
    like_query = "%" + query + "%"
    cur = conn.cursor()
    if field == "all":
        cur.execute(
            "SELECT * FROM pages WHERE title LIKE ? OR description LIKE ? OR keywords LIKE ? OR content LIKE ? OR html_content LIKE ?",
            (like_query, like_query, like_query, like_query, like_query)
        )
    elif field == "content":
        cur.execute("SELECT * FROM pages WHERE content LIKE ? OR html_content LIKE ?", (like_query, like_query))
    elif field in ["title", "description", "keywords"]:
        cur.execute(f"SELECT * FROM pages WHERE {field} LIKE ?", (like_query,))
    else:
        cur.execute(
            "SELECT * FROM pages WHERE title LIKE ? OR description LIKE ? OR keywords LIKE ? OR content LIKE ? OR html_content LIKE ?",
            (like_query, like_query, like_query, like_query, like_query)
        )
    results = cur.fetchall()
    return jsonify(results)

# --- Templates definieren und erstellen ---
# Diese Templates stammen aus der alten Version:

# --- Navbar und Logo ---
navbar_html = r"""
<div class="navbar">
    <a href="/">Startseite</a>
    <a href="/search">Websuche</a>
    <a href="/search_images">Bildersuche</a>
    <a href="/search_videos">Videosuche (Beta)</a>
    <a href="/manual_index">Manuelle Indexierung</a>
    <a href="/crawler_status">Crawler Status</a>
    <a href="/auto_mode">Automodus (Beta)</a>
</div>
"""

logo_html = r"""
<div class="logo" style="margin-bottom: 40px;">
    <img src="https://ww2.tmp-networks.de/assets/front/img/header_logo_16837078961785389093.png" 
         alt="TMP Networks" style="max-width: 200px; height: auto;">
</div>
"""

base_css = r"""
html, body {
    height: 100%;
    margin: 0;
    padding: 0;
    font-family: 'Roboto', sans-serif;
    background: linear-gradient(to bottom right, #e0f7fa, #e1bee7);
    color: #333;
}
header {
    text-align: center;
    padding: 20px;
}
.navbar {
    background: linear-gradient(90deg, #FF7E5F, #FEB47B);
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 15px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    border-radius: 0 0 10px 10px;
}
.navbar a {
    color: white;
    text-decoration: none;
    margin: 0 15px;
    font-size: 16px;
    padding: 10px 20px;
    transition: background 0.3s ease, transform 0.3s ease;
    border-radius: 5px;
    font-weight: 500;
}
.navbar a:hover {
    background: rgba(255, 255, 255, 0.2);
    transform: scale(1.05);
}
.container {
    flex: 1;
    max-width: 1200px;
    margin: 40px auto;
    padding: 20px;
    background: rgba(255,255,255,0.8);
    box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    border-radius: 10px;
}
.search-box {
    margin: 40px auto;
    display: flex;
    justify-content: center;
}
input[type="text"] {
    width: 80%;
    max-width: 600px;
    padding: 12px 20px;
    border: none;
    border-radius: 30px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    font-size: 16px;
    outline: none;
    transition: box-shadow 0.3s;
}
input[type="text"]:focus {
    box-shadow: 0 4px 6px rgba(0,0,0,0.2);
}
select {
    margin-top: 15px;
    padding: 8px;
    font-size: 14px;
    border-radius: 4px;
    border: 1px solid #ccc;
}
.button {
    background-color: #1a73e8;
    color: white;
    border: none;
    padding: 10px 20px;
    font-size: 14px;
    border-radius: 4px;
    cursor: pointer;
    margin-top: 20px;
    transition: background 0.3s ease;
}
.button:hover {
    background-color: #1669c1;
}
.flash {
    margin: 15px auto;
    padding: 10px;
    border-radius: 5px;
    max-width: 500px;
}
.flash.success {
    background: #d4edda;
    color: #155724;
}
.flash.error {
    background: #f8d7da;
    color: #721c24;
}
.flash.info {
    background: #d1ecf1;
    color: #0c5460;
}
.footer {
    text-align: center;
    padding: 10px;
    background: #e6e3f0;
    color: #555;
    font-size: 12px;
    width: 100%;
    position: fixed;
    bottom: 0;
}
.result {
    background: rgba(255,255,255,0.9);
    margin-bottom: 20px;
    padding: 15px;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    text-align: left;
}
.result a {
    font-size: 18px;
    color: #1a0dab;
    text-decoration: none;
}
.result a:hover {
    text-decoration: underline;
}
.url {
    font-size: 14px;
    color: #006621;
}
.snippet {
    font-size: 14px;
    color: #545454;
}
.pagination {
    margin-top: 20px;
    display: flex;
    justify-content: center;
    align-items: center;
}
.pagination a, .pagination span {
    margin: 0 5px;
    text-decoration: none;
    font-size: 14px;
}
"""

# HTML-Templates als Strings definieren
auto_mode_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>Automodus</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <style>
        """ + base_css + r"""
    </style>
</head>
<body>
    """ + navbar_html + r"""
    <div class="container">
        <h2>Automodus</h2>
        <p>Status: {{ "Aktiviert" if auto_mode_enabled else "Deaktiviert" }}</p>
        <p>Aktuelle Startdomain: {{ start_domain }}</p>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="flash {{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="post" action="/auto_mode">
            <input type="text" name="start_url" placeholder="https://example.com" value="{{ start_domain }}">
            <br>
            <label>
                <input type="checkbox" name="ignore_robots" value="1" {% if auto_ignore_robots %}checked{% endif %}>
                Robots.txt ignorieren
            </label>
            <br>
            <label>
                <input type="checkbox" name="clear_queue" value="1">
                Warteschlange leeren beim Deaktivieren
            </label>
            <br>
            <button class="button" type="submit" name="action" value="enable">Automodus Aktivieren</button>
            <button class="button" type="submit" name="action" value="disable">Automodus Deaktivieren</button>
        </form>
        {% if auto_mode_enabled %}
            <br>
            <form method="post" action="/start_auto">
                <button class="button" type="submit">Start Auto Crawl</button>
            </form>
        {% endif %}
    </div>
    <div class="footer">
        &copy; <script>document.write(new Date().getFullYear());</script> TMP-SYSTEM-SERVICE GmbH
    </div>
</body>
</html>
"""

index_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>TMP-Networks Search</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <link href="https://fonts.googleapis.com/css?family=Pacifico|Courier+Prime&display=swap" rel="stylesheet">
    <style>
        """ + base_css + r"""
        .log-container {
            max-width: 800px;
            margin: 50px auto;
            padding: 40px;
            background: rgba(255,255,255,0.9);
            border-radius: 10px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            font-family: 'Courier Prime', monospace;
        }
        .log-container h2 {
            margin-bottom: 20px;
            font-family: 'Pacifico', cursive;
            font-size: 36px;
            color: #333;
            text-align: center;
        }
        .log-container ul {
            list-style: none;
            padding: 0;
        }
        .log-container ul li {
            margin: 15px 0;
            font-size: 20px;
            color: #555;
        }
    </style>
</head>
<body>
    """ + navbar_html + r"""
    <div class="log-container">
        <h2>Versions Log - TMP-Networks Search</h2>
        <ul>
            <li>Version 1.0: Erste Veröffentlichung der Webscrapper APP</li>
            <li>Version 1.1: Verbesserte Threading und Fehlerbehandlung</li>
            <li>Version 1.2: Hinzufügen von Medien- und Video-Indexierung</li>
            <li>Version 1.3: Einführung des Automodus</li>
            <li>Version 1.4: Startseiten-Layout episch als Notizblock gestaltet</li>
            <li>Version 1.5: Offline-Snapshot-Funktionalität inkl. Offline-Link in den Suchergebnissen integriert</li>
            <li>Version 1.6: Suche erweitert – jetzt wird auch im Offline-Snapshot gesucht</li>
            <li>Version 1.7: Video-Indexierung verbessert – es werden nur echte Videoinhalte (inkl. HLS-Streams) indexiert</li>
            <li>Version 1.8: yt-dlp integriert, um HLS-Streams und HEVC/H.265 Videos zu erkennen</li>
            <li>Version 1.9: Verbesserte Videoindexierung mit Best-Quality Auswahl via yt_dlp und Performance-Log-Auswertung</li>
            <li>Version 2.0: Volltextsuche (FTS5) integriert für präzisere Suchergebnisse</li>
	    <li>Version 2.1: API Funktion hinzugefügt</li>
        </ul>
    </div></br>
    <div class="footer">
        &copy; <script>document.write(new Date().getFullYear());</script> TMP-SYSTEM-SERVICE GmbH
    </div>
</body>
</html>
"""

manual_index_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>Manuelle Indexierung</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <style>
        """ + base_css + r"""
    </style>
</head>
<body>
    """ + navbar_html + r"""
    <div class="container">
        <h2>Manuelle Indexierung</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="flash {{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="post">
            <input type="text" name="url" placeholder="URL eingeben" required>
            <br>
            <button class="button" type="submit">Indexierung starten</button>
        </form>
    </div>
    <div class="footer">
        &copy; <script>document.write(new Date().getFullYear());</script> TMP-SYSTEM-SERVICE GmbH
    </div>
</body>
</html>
"""

crawler_status_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>Crawler Status</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <meta http-equiv="refresh" content="5">
    <style>
        """ + base_css + r"""
    </style>
</head>
<body>
    """ + navbar_html + r"""
    <div class="container">
        <h2>Crawler Status</h2>
        {% if current %}
            <p>Aktuell wird verarbeitet: {{ current }}</p>
        {% else %}
            <p>Keine URL wird aktuell verarbeitet.</p>
        {% endif %}
        <p>Warteschlangenlänge: {{ queue_size }}</p>
        <p>Datenbankgröße: {{ file_size }}</p>
        <p>Indexierte Seiten: {{ count_pages }}</p>
        <p>Indexierte Bilder: {{ count_images }}</p>
        <p>Indexierte Videos: {{ count_videos }}</p>
        <hr>
        <h3>Verarbeitete URLs:</h3>
        <div style="text-align:left; margin:0 auto; max-width:1000px; 
                    background: rgba(255,255,255,0.9); padding:10px; border-radius:8px;">
            {% if log %}
                {% for url in log %}
                    {{ url }}{% if not loop.last %}<br>{% endif %}
                {% endfor %}
            {% else %}
                Keine URLs verarbeitet.
            {% endif %}
        </div>
        <hr>
        <h3>Registrierte Crawler (Peer):</h3>
        <div style="text-align:left; margin:0 auto; max-width:1000px; 
                    background: rgba(255,255,255,0.9); padding:10px; border-radius:8px;">
            {% if registered_crawlers %}
                <ul>
                    {% for crawler in registered_crawlers %}
                        <li>{{ crawler.crawler_name }} ({{ crawler.host }}) – IP: {{ crawler.ip }} – Zeit: {{ crawler.timestamp }}</li>
                    {% endfor %}
                </ul>
            {% else %}
                Keine registrierten Crawler.
            {% endif %}
        </div>
    </div>
    <div class="footer">
        &copy; <script>document.write(new Date().getFullYear());</script> TMP-SYSTEM-SERVICE GmbH
    </div>
</body>
</html>
"""

search_results_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>Suchergebnisse</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <style>
        """ + base_css + r"""
        .timestamp { font-size: 12px; color: #888; }
        .offline-container { text-align: left; margin-top: 5px; }
        .offline-link {
            font-size: 12px;
            line-height: 1.2;
            color: #999;
            text-decoration: none;
            border: 1px solid transparent;
            padding: 2px 4px;
            border-radius: 3px;
            transition: background 0.3s, color 0.3s;
            margin-right: 5px;
        }
        .offline-link:hover {
            background: #f0f0f0;
            color: #666;
        }
        fieldset {
            margin-top: 15px;
            padding: 10px;
            border: 1px solid #ccc;
            border-radius: 4px;
        }
        fieldset legend {
            font-weight: bold;
        }
    </style>
</head>
<body>
    """ + navbar_html + r"""
    <div class="container">
        """ + logo_html + r"""
        <div class="search-box">
            <form method="get" action="/search">
                <input type="text" name="q" value="{{ query }}" placeholder="Suchbegriff eingeben">
                <br>
                <select name="field">
                    <option value="all" {% if field == "all" %}selected{% endif %}>Alle Felder</option>
                    <option value="title" {% if field == "title" %}selected{% endif %}>Titel</option>
                    <option value="description" {% if field == "description" %}selected{% endif %}>Beschreibung</option>
                    <option value="keywords" {% if field == "keywords" %}selected{% endif %}>Keywords</option>
                    <option value="content" {% if field == "content" %}selected{% endif %}>Inhalt</option>
                </select>
                <select name="sort">
                    <option value="relevance" {% if sort == "relevance" %}selected{% endif %}>Relevanz</option>
                    <option value="newest" {% if sort == "newest" %}selected{% endif %}>Neueste</option>
                </select>
                <!-- Checkbox-Bereich für externe Server -->
                <fieldset>
                    <legend>Externe Server einbeziehen</legend>
                    {% for peer in peer_servers %}
                        <label>
                            <input type="checkbox" name="peers" value="{{ peer.url }}" {% if peer.url in selected_peers %}checked{% endif %}>
                            {{ peer.name }}
                        </label>
                    {% endfor %}
                </fieldset>
                <br>
                <button class="button" type="submit">Suchen</button>
            </form>
        </div>
        {% if query %}
            <p>Suchergebnisse für: <strong>{{ query }}</strong></p>
            <hr>
            {% for result in results %}
<div class="result">
    <!-- Titel -->
    <a href="{{ result.url }}" target="_blank">{{ result.title }}</a><br>
    <!-- URL -->
    <span class="url">{{ result.url }}</span><br>
    <!-- Beschreibung -->
    <span class="snippet">{{ result.description }}</span><br>
    <!-- Zeitstempel -->
    <span class="timestamp">Indexiert am: {{ result.indexed_at }}</span>
    <div class="offline-container">
        <!-- Offline-Link -->
        <a class="offline-link" href="/offline/{{ result.id }}" target="_blank">Offline Version anzeigen</a>
        <a class="offline-link" href="/offline/{{ result.id }}?text=1" target="_blank">Offline Only Text</a>
    </div>
</div>
{% endfor %}
            <div class="pagination">
                {% if page > 1 %}
                    <a class="button" href="{{ url_for('web_search', q=query, field=field, sort=sort, page=page-1) }}">Vorherige</a>
                {% endif %}
                <span>Seite {{ page }} von {{ total_pages }}</span>
                {% if page < total_pages %}
                    <a class="button" href="{{ url_for('web_search', q=query, field=field, sort=sort, page=page+1) }}">Nächste</a>
                {% endif %}
            </div><br>
        {% else %}
            <p>Keine Ergebnisse gefunden.</p>
        {% endif %}
    </div>
    <div class="footer">
        &copy; <script>document.write(new Date().getFullYear());</script> TMP-SYSTEM-SERVICE GmbH
    </div>
</body>
</html>
"""

search_images_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>Bildersuche</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <style>
        """ + base_css + r"""
        .image-result {
            margin: 20px;
            display: inline-block;
            text-align: center;
        }
        .image-result img {
            max-width: 200px;
            height: auto;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
    </style>
</head>
<body>
    """ + navbar_html + r"""
    <div class="container">
        """ + logo_html + r"""
        <div class="search-box">
            <form method="get" action="/search_images">
                <input type="text" name="q" value="{{ query }}" placeholder="Suchbegriff eingeben">
                <button class="button" type="submit">Suchen</button>
            </form>
        </div>
        <br>
        <hr>
        {% if results %}
            {% for result in results %}
                <div class="image-result">
                    <a href="{{ result[1] }}" target="_blank">
                        <img src="{{ result[2] }}" alt="{{ result[3] }}">
                    </a>
                    <p>{{ result[3] }}</p>
                    <a class="button" href="{{ result[2] }}" download>Download</a>
                </div>
            {% endfor %}
        {% else %}
            <p>Keine Bilder gefunden.</p>
        {% endif %}
        <div class="pagination">
            {% if page > 1 %}
                <a class="button" href="{{ url_for('search_images', q=query, page=page-1) }}">Vorherige</a>
            {% endif %}
            <span>Seite {{ page }} von {{ total_pages }}</span>
            {% if page < total_pages %}
                <a class="button" href="{{ url_for('search_images', q=query, page=page+1) }}">Nächste</a>
            {% endif %}
        </div>
        <br>
    </div>
    <div class="footer">
        &copy; <script>document.write(new Date().getFullYear());</script> TMP-SYSTEM-SERVICE GmbH
    </div>
</body>
</html>
"""

search_videos_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>Videosuche</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <style>
        """ + base_css + r"""
        .video-result {
            margin: 20px;
            display: inline-block;
            text-align: center;
        }
        .video-result video {
            max-width: 400px;
            width: 100%;
            height: auto;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
</head>
<body>
    """ + navbar_html + r"""
    <div class="container">
        """ + logo_html + r"""
        <div class="search-box">
            <form method="get" action="/search_videos">
                <input type="text" name="q" value="{{ query }}" placeholder="Suchbegriff eingeben">
                <button class="button" type="submit">Suchen</button>
            </form>
        </div>
        <br>
        <hr>
        {% if results %}
            {% for result in results %}
                <div class="video-result">
                    <h3>{{ result[3] }}</h3>
                    <p>{{ result[4] }}</p>
                    {% if result[2].endswith('.m3u8') %}
                        <video id="video-{{ loop.index }}" controls></video>
                        <script>
                            (function() {
                                var video = document.getElementById('video-{{ loop.index }}');
                                var videoSrc = "{{ result[2] }}";
                                if (Hls.isSupported()) {
                                    var hls = new Hls();
                                    hls.loadSource(videoSrc);
                                    hls.attachMedia(video);
                                } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                                    video.src = videoSrc;
                                }
                            })();
                        </script>
                    {% else %}
                        <iframe src="{{ result[2] }}" frameborder="0" allowfullscreen></iframe>
                    {% endif %}
                    <br>
                    <a class="button" href="{{ result[2] }}" download>Download</a>
                </div>
            {% endfor %}
        {% else %}
            <p>Keine Videos gefunden.</p>
        {% endif %}
        <div class="pagination">
            {% if page > 1 %}
                <a class="button" href="{{ url_for('search_videos', q=query, page=page-1) }}">Vorherige</a>
            {% endif %}
            <span>Seite {{ page }} von {{ total_pages }}</span>
            {% if page < total_pages %}
                <a class="button" href="{{ url_for('search_videos', q=query, page=page+1) }}">Nächste</a>
            {% endif %}
        </div>
        <br>
    </div>
    <div class="footer">
        &copy; <script>document.write(new Date().getFullYear());</script> TMP-SYSTEM-SERVICE GmbH
    </div>
</body>
</html>
"""

offline_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>Offline Snapshot</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <style>
        body { margin: 0; padding: 0; }
        .header {
            padding: 10px;
            background: #f0f0f0;
            text-align: center;
        }
        iframe {
            width: 100%;
            height: 90vh;
            border: none;
        }
    </style>
</head>
<body>
    <div class="header">
        <p>Offline Snapshot der Seite: <a href="{{ original_url }}" target="_blank">{{ original_url }}</a></p>
        <p><a href="javascript:history.back()">Zurück</a></p>
    </div>
    <iframe srcdoc="{{ html_content|e }}"></iframe>
</body>
</html>
"""

offline_text_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>Offline Snapshot - Nur Text</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <style>
        body { margin: 0; padding: 0; font-family: sans-serif; }
        .header {
            padding: 10px;
            background: #f0f0f0;
            text-align: center;
        }
        pre {
            white-space: pre-wrap;
            word-wrap: break-word;
            padding: 20px;
            background: #fff;
            margin: 20px;
            border: 1px solid #ccc;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <div class="header">
        <p>Offline Snapshot der Seite: <a href="{{ original_url }}" target="_blank">{{ original_url }}</a></p>
        <p><a href="javascript:history.back()">Zurück</a></p>
    </div>
    <pre>{{ plain_text }}</pre>
</body>
</html>
"""

search_results_task_html = r"""<!DOCTYPE html>
<html>
<head>
    <title>Suchergebnisse für Task {{ task_id }}</title>
    <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
    <style>
        """ + base_css + r"""
        .timestamp { font-size: 12px; color: #888; }
        .result {
            background: rgba(255,255,255,0.9);
            margin-bottom: 20px;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: left;
        }
        .result a {
            font-size: 18px;
            color: #1a0dab;
            text-decoration: none;
        }
        .result a:hover {
            text-decoration: underline;
        }
        .url { font-size: 14px; color: #006621; }
        .snippet { font-size: 14px; color: #545454; }
    </style>
</head>
<body>
    """ + navbar_html + r"""
    <div class="container">
        <h2>Suchergebnisse für Task {{ task_id }}</h2>
        {% if results %}
            {% for result in results %}
                <div class="result">
                    <a href="{{ result.url }}" target="_blank">{{ result.title }}</a><br>
                    <span class="url">{{ result.url }}</span><br>
                    <span class="snippet">{{ result.description }}</span><br>
                    <span class="timestamp">Indexiert am: {{ result.indexed_at }}</span>
                </div>
            {% endfor %}
        {% else %}
            <p>Keine Ergebnisse gefunden.</p>
        {% endif %}
    </div>
    <div class="footer">
        &copy; <script>document.write(new Date().getFullYear());</script> TMP-SYSTEM-SERVICE GmbH
    </div>
</body>
</html>
"""

# Template für den Ladebildschirm der Task
task_status_html = r"""<!DOCTYPE html>
   <html>
   <head>
       <title>Suche läuft...</title>
       <link rel="icon" type="image/png" href="https://ww2.tmp-networks.de/assets/front/img/fav_icon_1683708200522907507.png">
       <style>
           body { font-family: Arial, sans-serif; text-align: center; padding-top: 100px; }
           .loader { font-size: 24px; }
           .timeout-message { color: red; font-size: 18px; margin-top: 20px; }
       </style>
       <script>
   const TIMEOUT = 30000; // Max 30 Sek
   let startTime = Date.now();
   
   function pollResults() {
       fetch("/get_task_results/{{ task_id }}")
       .then(response => response.json())
       .then(data => {
           if (data.status === "completed") {
               window.location.href = "/search_results?task_id={{ task_id }}";
           } else {
               if (Date.now() - startTime < TIMEOUT) {
                   setTimeout(pollResults, 5000); // alle 5 Sekunden abfragen
               } else {
                   alert("Keine Ergebnisse in der Zeit gefunden.");
               }
           }
       });
   }
   window.onload = () => setTimeout(pollResults, 3000); // erste Abfrage nach 3 Sekunden
</script>
   </head>
   <body>
       <div id="loader" class="loader">Die Suchaufgabe wird an die Server verteilt. Bitte warten...</div>
       <div id="timeout" class="timeout-message" style="display: none;">
           Es wurden leider keine Ergebnisse innerhalb von 30 Sekunden gefunden.
           <br>
           <a href="/">Zurück zur Startseite</a>
       </div>
   </body>
   </html>"""

# HTML-Templates in Dateien speichern
if not os.path.exists("templates"):
    os.makedirs("templates")

with open("templates/index.html", "w", encoding="utf-8") as f:
    f.write(index_html)

with open("templates/manual_index.html", "w", encoding="utf-8") as f:
    f.write(manual_index_html)

with open("templates/crawler_status.html", "w", encoding="utf-8") as f:
    f.write(crawler_status_html)

with open("templates/auto_mode.html", "w", encoding="utf-8") as f:
    f.write(auto_mode_html)

with open("templates/search_results.html", "w", encoding="utf-8") as f:
    f.write(search_results_html)

with open("templates/search_images.html", "w", encoding="utf-8") as f:
    f.write(search_images_html)

with open("templates/search_videos.html", "w", encoding="utf-8") as f:
    f.write(search_videos_html)

with open("templates/offline.html", "w", encoding="utf-8") as f:
    f.write(offline_html)

with open("templates/offline_text.html", "w", encoding="utf-8") as f:
    f.write(offline_text_html)

with open("templates/task_status.html", "w", encoding="utf-8") as f:
    f.write(task_status_html)

with open("templates/search_results_task.html", "w", encoding="utf-8") as f:
    f.write(search_results_task_html)

# --- Hauptblock ---
if __name__ == "__main__":
    # Starte Hintergrund-Threads, Registrierungen usw.
    threading.Thread(target=poll_for_tasks, daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    register_crawler()

    # Wichtiger Hinweis: Debug und ReLoader ausschalten!
    app.run(debug=False, host="0.0.0.0", port=7001, use_reloader=False)
