#!/bin/bash

# Ensure we always run from the project root directory
cd "$(dirname "$0")" || exit 1

# Configuration
VENV_DIR=".venv"
APP_FILE="app.py"

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
    uv sync
    echo -e "${GREEN}🏃 Running $APP_FILE...${NC}"
    if command -v entr &> /dev/null; then
        echo -e "${BLUE}🔄 Auto-reload enabled via entr${NC}"
        find . -type d \( -name ".venv" -o -name ".git" -o -name "__pycache__" \) -prune -o -type f \( -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" \) -print | entr -r uv run python $APP_FILE
    else
        uv run python $APP_FILE
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
    # Since pyproject.toml is modern, we try pip install -e . or pip install dependencies
    echo "🛡️ Installing/Updating dependencies..."
    pip install --upgrade pip
    
    # Extract dependencies from pyproject.toml if possible, or just app requirements
    if [ -f "pyproject.toml" ]; then
        # Try to install everything from pyproject.toml
        # If it lacks a build system, we might need a workaround or just install list
        pip install flask opencv-python ultralytics requests
    fi

    # 4. Run
    echo -e "${GREEN}🏃 Running $APP_FILE...${NC}"
    if command -v entr &> /dev/null; then
        echo -e "${BLUE}🔄 Auto-reload enabled via entr${NC}"
        find . -type d \( -name ".venv" -o -name ".git" -o -name "__pycache__" \) -prune -o -type f \( -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" \) -print | entr -r python $APP_FILE
    else
        python $APP_FILE
    fi
fi
