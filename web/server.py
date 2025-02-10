import os
import subprocess
import sys
import threading
import zipfile

import pyzipper
import rarfile
from flask import Flask, request, render_template_string, abort, send_file, stream_with_context, Response
from werkzeug.utils import secure_filename
from io import BytesIO

ZIP_PASSWORD = b'pixoprotect'
TIME_LIMIT = 3600  # Server will automatically terminate after 1 hour

# Main HTML page with two forms: one for multi‑file upload and one for starting the process.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Project Interface</title>
</head>
<body>
    <h2>Upload Files</h2>
    <form method="POST" action="/upload" enctype="multipart/form-data">
      <input type="hidden" name="token" value="{{ token }}" />
      <input type="file" name="upload_files" multiple />
      <button type="submit">Upload Files</button>
    </form>
    <hr>
    <h2>Start Process</h2>
    <form method="POST" action="/start">
      <input type="hidden" name="token" value="{{ token }}" />
      <button type="submit">Start</button>
    </form>
    <hr>
    <h2>Finish and Shut Down Server</h2>
    <form method="POST" action="/shutdown">
      <input type="hidden" name="token" value="{{ token }}" />
      <button type="submit">Finish</button>
    </form>
</body>
</html>
"""

# HTML template to display upload results along with a Back button.
UPLOAD_RESULT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Upload Results</title>
</head>
<body>
    <h2>Files uploaded successfully:</h2>
    <ul>
      {% for file in uploaded_filenames %}
         <li>{{ file }}</li>
      {% endfor %}
    </ul>
    <button onclick="window.location.href='/?token={{ token }}'">Back</button>
</body>
</html>
"""


def create_app(upload_token: str, upload_folder: str, project_dir: str) -> Flask:
    """
    Create and configure a Flask app that:
      - Requires the provided upload_token to access any page.
      - Saves uploaded files to upload_folder.
      - Runs the main script (main.py) from project_dir when requested.
    """
    app = Flask(__name__)
    app.config["UPLOAD_FOLDER"] = upload_folder
    app.config["PROJECT_DIR"] = project_dir
    app.config["ZIP_PASSWORD"] = ZIP_PASSWORD

    @app.route("/", methods=["GET"])
    def index():
        token = request.args.get("token", "")
        if token != upload_token:
            return abort(403, description="Invalid or missing token")
        return render_template_string(HTML_TEMPLATE, token=upload_token)

    @app.route("/upload", methods=["POST"])
    def upload():
        token = request.form.get("token", "")
        if token != upload_token:
            return abort(403, description="Invalid or missing token")

        # Use getlist to support multiple file uploads.
        files = request.files.getlist("upload_files")
        if not files or all(file.filename == "" for file in files):
            return "No selected file", 400

        def extract_archive(archive_file, destination, archive_type):
            try:
                if archive_type == 'zip':
                    archive_data = BytesIO(archive_file.read())
                    with pyzipper.AESZipFile(archive_data) as zf:
                        if zf.namelist()[0].startswith('__MACOSX'):
                            return False, "MacOS hidden files detected. Skipping extraction."
                        try:
                            zf.pwd = ZIP_PASSWORD
                            zf.extractall(path=destination)
                        except RuntimeError:
                            return False, "Incorrect password for zip file. File deleted."
                    return True, "Archive extracted successfully."
                elif archive_type == 'rar':
                    archive_data = BytesIO(archive_file.read())
                    with rarfile.RarFile(archive_data) as rf:
                        try:
                            rf.extractall(path=destination, pwd=app.config["ZIP_PASSWORD"])
                        except rarfile.BadRarFile:
                            return False, "Incorrect password for rar file. File deleted."
                    return True, "Archive extracted successfully."
            except (zipfile.BadZipFile, rarfile.BadRarFile):
                return False, f"Not a valid {archive_type} file."
            except Exception as e:
                return False, f"Error extracting {archive_type}: {str(e)}"

        uploaded_filenames = []
        for file in files:
            if file and file.filename:
                filename = secure_filename(file.filename)
                if filename.lower().endswith(('.zip', '.rar')):
                    archive_type = 'zip' if filename.lower().endswith('.zip') else 'rar'
                    success, message = extract_archive(file, app.config["UPLOAD_FOLDER"], archive_type)
                    if not success:
                        try:
                            os.remove(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                        except Exception as e:
                            print(f"Error deleting file: {e}")

                    if "Incorrect password" in message:
                        uploaded_filenames.append(f"{filename} (deleted: {message})")
                    else:
                        uploaded_filenames.append(
                            f"{filename} ({'extracted' if success else 'not extracted'}: {message})")
                else:
                    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                    file.save(save_path)
                    uploaded_filenames.append(filename)

        if uploaded_filenames:
            return render_template_string(
                UPLOAD_RESULT_TEMPLATE,
                uploaded_filenames=uploaded_filenames,
                token=upload_token
            )
        else:
            return "No files uploaded.", 400

    @app.route("/start", methods=["POST"])
    def start():
        token = request.form.get("token", "")
        if token != upload_token:
            return abort(403, description="Invalid or missing token")
        try:
            python_executable = sys.executable

            # server.py is in pixoDocumentToolsV3/web, so the project root is one level up.
            web_dir = os.path.dirname(os.path.abspath(__file__))  # pixoDocumentToolsV3/web
            project_root = os.path.abspath(os.path.join(web_dir, os.pardir))  # pixoDocumentToolsV3

            # The test folder is provided via app.config["PROJECT_DIR"],
            # e.g., "test5(e86de6b7-7496-47bd-8a1e-2b32ea832f37)"
            test_folder = app.config["PROJECT_DIR"]
            main_script_path = os.path.join(project_root, test_folder, "main.py")

            # Create bootstrap code that:
            # 1. Inserts the project root into sys.path so that imports (e.g. "from cfg import load_config") work.
            # 2. Executes the contents of main.py.
            bootstrap_code = (
                "import sys; "
                "sys.path.insert(0, r'{}'); "
                "exec(open(r'{}').read())"
            ).format(project_root, main_script_path)

            # Prepare the environment so that the project root is in PYTHONPATH (optional but can help)
            env = os.environ.copy()
            existing_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = project_root + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

            # Use the '-u' flag to force unbuffered output.
            cmd = [python_executable, "-u", "-c", bootstrap_code]

            def generate():
                # Begin HTML output.
                yield "<html><head><title>Main Script Output</title></head><body><pre>\n"
                # Launch the process.
                with subprocess.Popen(
                        cmd,
                        cwd=project_root,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True
                ) as proc:
                    # Stream each line as it's available.
                    for line in iter(proc.stdout.readline, ""):
                        # Optionally, you can wrap or format the line.
                        yield line
                # End the HTML output.
                yield "</pre><br>"
                yield "<button onclick=\"window.location.href='/?token={}'\">Back</button>".format(upload_token)
                yield "</body></html>"

            # Return a streaming response.
            return Response(stream_with_context(generate()), mimetype="text/html")
        except Exception as e:
            return f"Error running main script: {str(e)}", 500

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        """
        When this route is POSTed to with the correct token or accessed via the Finish button,
        the Flask development server is shut down.
        """
        token = request.form.get("token", "")
        if token != upload_token:
            return abort(403, description="Invalid or missing token")
        shutdown_func = request.environ.get("werkzeug.server.shutdown")
        if shutdown_func is None:
            return "Not running with the Werkzeug Server", 500
        threading.Thread(target=shutdown_func).start()
        return "Server shutting down..."

    return app


def run_server(upload_token: str, upload_folder: str, project_dir: str, host="127.0.0.1", port=5000):
    """
    Create the Flask app with the given token, upload folder, and project folder.
    Then, run the app. The server will automatically shut down after TIME_LIMIT seconds.
    """
    app = create_app(upload_token, upload_folder, project_dir)
    print(f"Starting web interface on http://{host}:{port}?token={upload_token}")
    print("Press Ctrl+C to stop the server.")

    # Start a background thread that will shut down the server after TIME_LIMIT seconds.
    import threading
    import time
    def shutdown_after_timeout():
        time.sleep(TIME_LIMIT)
        try:
            import requests
            shutdown_url = f"http://{host}:{port}/shutdown"
            # Post the token to the shutdown route
            requests.post(shutdown_url, data={"token": upload_token})
        except Exception as e:
            print("Error shutting down server:", e)

    threading.Thread(target=shutdown_after_timeout, daemon=True).start()

    app.run(host=host, port=port, debug=False)
