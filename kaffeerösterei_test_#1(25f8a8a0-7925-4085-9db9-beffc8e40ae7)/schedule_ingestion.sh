#!/bin/bash
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$PROJECT_DIR")"
echo "Project Directory: $PROJECT_DIR"
echo "Project Root: $PROJECT_ROOT"

cd "$PROJECT_DIR"
source "$PROJECT_ROOT/venv/bin/activate"

python -u -c "import sys; sys.path.insert(0, r'$PROJECT_ROOT%'); exec(open(r'$PROJECT_DIR/main.py', encoding='utf-8').read())"
