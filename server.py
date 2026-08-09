from flask import Flask, render_template, jsonify, request
import os
import threading
import logging
from datetime import datetime

# استيراد أداة الفحص
import ggd

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# حالة الفحص
running = False
current_combos = []

# ============================================================
# دوال جلب الإحصائيات
# ============================================================
def get_stats():
    with ggd.stats._lock:
        return {
            "total": len(current_combos),
            "hits": ggd.stats.hits,
            "twofa": ggd.stats.twofa,
            "bad": ggd.stats.bad,
            "progress": int((ggd.stats.checked / max(1, len(current_combos))) * 100),
            "status": "SCANNING" if running else "COMPLETED" if ggd.stats.checked > 0 else "READY",
            "services": {
                "Minecraft": ggd.stats.minecraft,
                "GamePass": ggd.stats.gamepass,
                "Xbox": ggd.stats.xbox,
                "NotLinked": ggd.stats.not_linked,
            }
        }

def get_logs():
    with ggd.stats._lock:
        return [
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Checked: {ggd.stats.checked}", "type": "info"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Hits: {ggd.stats.hits}", "type": "ok"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Bad: {ggd.stats.bad}", "type": "warn"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"Minecraft: {ggd.stats.minecraft}", "type": "ok"},
            {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"GamePass: {ggd.stats.gamepass}", "type": "ok"},
        ]

# ============================================================
# تشغيل الفحص في خيط منفصل
# ============================================================
def run_checker():
    global running
    if not current_combos:
        return
    running = True
    try:
        ggd.run_check(current_combos)
    except Exception as e:
        logger.error(f"Error in checker: {e}")
    finally:
        running = False

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
    f = request.files['file']
    if f.filename == '':
        return jsonify({"error": "No file selected"}), 400
    f.save('combos.txt')
    return jsonify({"message": "File uploaded successfully!"})

@app.route('/api/start', methods=['POST'])
def start_check():
    global current_combos, running
    if running:
        return jsonify({"status": "already_running"})
    
    combo_file = "combos.txt"
    if not os.path.exists(combo_file):
        return jsonify({"status": "error", "message": "Please upload combos.txt first"})
    
    with open(combo_file, 'r', encoding='utf-8', errors='ignore') as f:
        current_combos = [line.strip() for line in f if line.strip() and ':' in line]
    
    if not current_combos:
        return jsonify({"status": "error", "message": "No valid combos found"})
    
    # تشغيل الفحص في خيط جديد
    threading.Thread(target=run_checker, daemon=True).start()
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
    with ggd.stats._lock:
        ggd.stats.checked = ggd.stats.hits = ggd.stats.bad = ggd.stats.twofa = ggd.stats.minecraft = ggd.stats.gamepass = ggd.stats.xbox = ggd.stats.not_linked = ggd.stats.errors = 0
    return jsonify({"status": "reset"})

# ============================================================
# التشغيل
# ============================================================
if __name__ == '__main__':
    print("""
    ═══════════════════════════════════════════
      DroopFile Dashboard
      URL: http://127.0.0.1:5000
    ═══════════════════════════════════════════
    """)
    app.run(debug=True, host='0.0.0.0', port=5000)
