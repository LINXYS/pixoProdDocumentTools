import threading
import time
import requests
from .app_factory import create_app

TIME_LIMIT = 3600  # Server will automatically terminate after 1 hour

def run_server(upload_token: str, project_dir: str, host="127.0.0.1", port=5000):
    """
    Create the Flask app with the given token, upload folder, and project folder.
    Then, run the app. The server will automatically shut down after TIME_LIMIT seconds.
    """
    app = create_app(upload_token, project_dir)
    print("Project directory:", project_dir)
    print(f"Starting web interface on http://{host}:{port}?token={upload_token}")
    print(f"Try opening in your browser on http://dev.pixoai.ch:5001?token={upload_token}")
    print("Press Ctrl+C to stop the server.")

    def shutdown_after_timeout():
        time.sleep(TIME_LIMIT)
        try:
            shutdown_url = f"http://{host}:{port}/shutdown"
            requests.post(shutdown_url, data={"token": upload_token})
        except Exception as e:
            print("Error shutting down server:", e)

    threading.Thread(target=shutdown_after_timeout, daemon=True).start()

    app.run(host=host, port=port, debug=False)
