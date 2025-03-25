#!/bin/bash
    # Start the web interface for file uploads and process.
    # The server will automatically terminate after 1 hour.

    # Move to the directory containing this script
    cd "$(dirname "$0")"

    # Go up ONE level to the project root (adjust '..' as needed)
    cd ..

    # Activate the virtual environment
    source venv/bin/activate

    # Run the server, passing the required parameters
    python -c "from web.server import run_server; run_server('LNtnHm8SBIrLGVaUjxiFew', '.')"
    