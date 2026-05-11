#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  STMCV — Smart Traffic Management & Computer Vision
#  Startup Script v2.0
# ══════════════════════════════════════════════════════════════════════════════

cd "$(dirname "$0")" || exit 1

# ── Color Palette ─────────────────────────────────────────────────────────────
R='\033[0;31m'   # Red
G='\033[0;32m'   # Green
Y='\033[0;33m'   # Yellow
B='\033[0;34m'   # Blue
M='\033[0;35m'   # Magenta
C='\033[0;36m'   # Cyan
W='\033[1;37m'   # Bold White
D='\033[2;37m'   # Dim White
NC='\033[0m'     # Reset

VENV_DIR=".venv"
APP_FILE="app.py"
TUI_FILE="tui_dashboard.py"
APP_PORT=5050

# ── Helper Functions ──────────────────────────────────────────────────────────
section()  { echo -e "\n${B}┌─ ${W}$1${NC}"; }
item_ok()  { echo -e "${B}│  ${G}✔  ${NC}$1"; }
item_warn(){ echo -e "${B}│  ${Y}⚠  ${NC}$1"; }
item_err() { echo -e "${B}│  ${R}✘  ${NC}$1"; }
item_info(){ echo -e "${B}│  ${C}ℹ  ${NC}$1"; }
divider()  { echo -e "${D}────────────────────────────────────────────────────────────${NC}"; }

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo -e "${C}"
echo "  ███████╗████████╗███╗   ███╗ ██████╗██╗   ██╗"
echo "  ██╔════╝╚══██╔══╝████╗ ████║██╔════╝██║   ██║"
echo "  ███████╗   ██║   ██╔████╔██║██║     ██║   ██║"
echo "  ╚════██║   ██║   ██║╚██╔╝██║██║     ╚██╗ ██╔╝"
echo "  ███████║   ██║   ██║ ╚═╝ ██║╚██████╗ ╚████╔╝ "
echo "  ╚══════╝   ╚═╝   ╚═╝     ╚═╝ ╚═════╝  ╚═══╝  "
echo -e "${NC}"
echo -e "  ${W}Smart Traffic Management & Computer Vision${NC}"
echo -e "  ${D}$(date '+%A, %d %B %Y  |  %H:%M:%S %Z')${NC}"
divider

# ── 1. ENVIRONMENT ────────────────────────────────────────────────────────────
section "ENVIRONMENT"
item_info "Working directory : $(pwd)"
item_info "OS                : $(uname -srm)"
item_info "Hostname          : $(hostname)"

PYTHON=""
if command -v uv &>/dev/null; then
    PYTHON="uv run python"
    item_ok "Runtime  : uv $(uv --version 2>&1 | awk '{print $2}')"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
    item_ok "Runtime  : Python $(python3 --version 2>&1 | awk '{print $2}')"
else
    item_err "No Python or uv found — aborting."
    exit 1
fi

# ── 2. SYSTEM RESOURCES ───────────────────────────────────────────────────────
section "SYSTEM RESOURCES"
CPU_CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "?")
RAM_TOTAL=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo "?")
RAM_FREE=$(free -m  2>/dev/null | awk '/^Mem:/{print $4}' || echo "?")
DISK_USE=$(df -h . 2>/dev/null | awk 'NR==2{print $5 " used of " $2}' || echo "?")

item_info "CPU cores : ${CPU_CORES}"
item_info "RAM       : ${RAM_FREE} MB free / ${RAM_TOTAL} MB total"
item_info "Disk      : ${DISK_USE}"

# GPU check
if command -v nvidia-smi &>/dev/null; then
    GPU=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)
    [ -n "$GPU" ] && item_ok "GPU       : $GPU" || item_warn "GPU       : nvidia-smi found but no GPU detected"
else
    item_warn "GPU       : No NVIDIA GPU detected — YOLO will run on CPU"
fi

# ── 3. NETWORK ────────────────────────────────────────────────────────────────
section "NETWORK"
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "unknown")
item_info "Local IP  : ${LOCAL_IP}"
item_info "HMI URL   : http://${LOCAL_IP}:${APP_PORT}"
item_info "API URL   : http://${LOCAL_IP}:${APP_PORT}/api/lane_counts"

# Check if port is already in use
if ss -tlnp 2>/dev/null | grep -q ":${APP_PORT} " || netstat -tlnp 2>/dev/null | grep -q ":${APP_PORT} "; then
    item_warn "Port ${APP_PORT} is already in use — old process may still be running"
    OLD_PID=$(ss -tlnp 2>/dev/null | grep ":${APP_PORT} " | grep -oP 'pid=\K[0-9]+' | head -1)
    [ -n "$OLD_PID" ] && item_warn "Stale PID : ${OLD_PID} (you may want to kill it first)"
else
    item_ok "Port ${APP_PORT} is free and ready"
fi

# ── 4. DEPENDENCIES ───────────────────────────────────────────────────────────
section "DEPENDENCIES"
DEPS="flask opencv-python ultralytics requests rich psutil"

if command -v uv &>/dev/null; then
    item_info "Syncing via uv..."
    uv add $DEPS --quiet 2>&1 | tail -3
    uv sync --quiet
    item_ok "uv dependency sync complete"
else
    # Activate or create venv
    if [ ! -d "$VENV_DIR" ]; then
        item_info "Creating virtual environment at ${VENV_DIR}..."
        python3 -m venv "$VENV_DIR"
    fi
    source "$VENV_DIR/bin/activate"
    item_ok "Virtual environment activated"

    item_info "Checking/installing packages..."
    pip install --quiet --upgrade pip
    MISSING=()
    for pkg in flask opencv-python ultralytics requests rich psutil; do
        if ! pip show "$pkg" &>/dev/null; then MISSING+=("$pkg"); fi
    done

    if [ ${#MISSING[@]} -gt 0 ]; then
        item_warn "Installing missing: ${MISSING[*]}"
        pip install --quiet "${MISSING[@]}"
    fi
    item_ok "All Python packages satisfied"
fi

# Verify key imports
section "MODULE VERIFICATION"
for mod in flask cv2 ultralytics rich psutil requests; do
    if $PYTHON -c "import $mod" &>/dev/null; then
        VER=$($PYTHON -c "import $mod; v=getattr($mod,'__version__',None) or getattr(__import__('importlib.metadata',fromlist=['version']),'version','?')('$mod'); print(v)" 2>/dev/null || echo "ok")
        item_ok "$mod  ${D}(${VER})${NC}"
    else
        item_err "$mod  — FAILED TO IMPORT"
    fi
done

# ── 5. PROJECT FILES ──────────────────────────────────────────────────────────
section "PROJECT FILES"
for f in "$APP_FILE" "$TUI_FILE" "detection.py" "templates/index.html" "static/js/script.js"; do
    if [ -f "$f" ]; then
        SIZE=$(du -sh "$f" 2>/dev/null | cut -f1)
        item_ok "$f  ${D}(${SIZE})${NC}"
    else
        item_err "$f  — NOT FOUND"
    fi
done

# Database check
if [ -f "traffic_data.db" ]; then
    DB_SIZE=$(du -sh traffic_data.db 2>/dev/null | cut -f1)
    item_ok "traffic_data.db  ${D}(${DB_SIZE})${NC}"
else
    item_warn "traffic_data.db not found — will be created on first run"
fi

# YOLO model
YOLO_MODEL=$(python3 -c "
import sqlite3, sys
try:
    c = sqlite3.connect('traffic_data.db')
    r = c.execute(\"SELECT value FROM config WHERE key='yolo_model'\").fetchone()
    print(r[0] if r else 'yolov8n.pt (default)')
except: print('unknown')
" 2>/dev/null)
if [ -f "$YOLO_MODEL" ]; then
    MODEL_SIZE=$(du -sh "$YOLO_MODEL" 2>/dev/null | cut -f1)
    item_ok "YOLO model: ${YOLO_MODEL}  ${D}(${MODEL_SIZE})${NC}"
else
    item_warn "YOLO model: ${YOLO_MODEL}  ${D}(will auto-download if needed)${NC}"
fi

# ── 6. AUTO-RELOAD ────────────────────────────────────────────────────────────
section "AUTO-RELOAD"
if command -v entr &>/dev/null; then
    item_ok "entr detected — file-watcher auto-reload ENABLED"
    ENTR_AVAILABLE=true
else
    item_warn "entr not installed — auto-reload DISABLED"
    item_info "Install with: sudo apt install entr"
    ENTR_AVAILABLE=false
fi

# ── 7. LAUNCH SUMMARY ─────────────────────────────────────────────────────────
divider
echo -e "\n  ${W}🚀  Launching STMCV Dashboard...${NC}"
echo -e "  ${G}➜  HMI  :  http://${LOCAL_IP}:${APP_PORT}${NC}"
echo -e "  ${G}➜  API  :  http://${LOCAL_IP}:${APP_PORT}/api/lane_counts${NC}"
echo -e "  ${D}   PID will be shown below after startup${NC}\n"
divider
echo

# ── 8. RUN ────────────────────────────────────────────────────────────────────
if command -v uv &>/dev/null; then
    if $ENTR_AVAILABLE; then
        find . -type d \( -name ".venv" -o -name ".git" -o -name "__pycache__" \) -prune \
          -o -type f \( -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" \) -print \
          | entr -r uv run python "$TUI_FILE"
    else
        uv run python "$TUI_FILE"
    fi
else
    if $ENTR_AVAILABLE; then
        find . -type d \( -name ".venv" -o -name ".git" -o -name "__pycache__" \) -prune \
          -o -type f \( -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" \) -print \
          | entr -r python "$TUI_FILE"
    else
        python "$TUI_FILE"
    fi
fi
