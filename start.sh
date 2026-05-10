#!/bin/bash

# Ensure we always run from the project root directory
cd "$(dirname "$0")" || exit 1

# Configuration
VENV_DIR=".venv"
APP_FILE="app.py"
TUI_FILE="tui_dashboard.py"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 STMCV Traffic Monitoring - Startup Sequence${NC}"

# Check if uv is available
if command -v uv &> /dev/null
then
    echo -e "${GREEN}✨ uv detected - using it for maximum speed${NC}"
    echo "📦 Syncing dependencies..."
    uv add rich psutil requests flask opencv-python ultralytics
    uv sync
    echo -e "${GREEN}🏃 Running $TUI_FILE...${NC}"
    if command -v entr &> /dev/null; then
        echo -e "${BLUE}🔄 Auto-reload enabled via entr${NC}"
        find . -type d \( -name ".venv" -o -name ".git" -o -name "__pycache__" \) -prune -o -type f \( -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" \) -print | entr -r uv run python $TUI_FILE
    else
        uv run python $TUI_FILE
    fi
else
    # Fallback to standard python/venv
    echo -e "${BLUE}🐍 uv not found - falling back to standard python venv${NC}"
    
    # 1. Create venv if missing
    if [ ! -d "$VENV_DIR" ]; then
        echo "🌑 Creating virtual environment..."
        python3 -m venv $VENV_DIR
    fi

    # 2. Activate
    echo "🔌 Activating environment..."
    source $VENV_DIR/bin/activate

    # 3. Install requirements
    echo "🛡️ Installing/Updating dependencies..."
    pip install --upgrade pip
    pip install flask opencv-python ultralytics requests rich psutil

    # 4. Run
    echo -e "${GREEN}🏃 Running $TUI_FILE...${NC}"
    if command -v entr &> /dev/null; then
        echo -e "${BLUE}🔄 Auto-reload enabled via entr${NC}"
        find . -type d \( -name ".venv" -o -name ".git" -o -name "__pycache__" \) -prune -o -type f \( -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" \) -print | entr -r python $TUI_FILE
    else
        python $TUI_FILE
    fi
fi
