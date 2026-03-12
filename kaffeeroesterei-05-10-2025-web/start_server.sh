#!/bin/bash
# Start the web interface for file uploads and processing.

# Move to the directory containing this script
cd "$(dirname "$0")"

# Go up ONE level to the project root
cd ..

# Activate the virtual environment
source venv/bin/activate

# Run the server, passing the required parameters
python -c "from web.server import run_server; run_server('vyJMAI5R0ILfSkG95MQnJA', 'kaffeeroesterei-05-10-2025-web', host='127.0.0.1', port=5000)"
