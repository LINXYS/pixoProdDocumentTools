import os
import subprocess
import zipfile
from flask import Flask, request, render_template_string, abort, send_file
from werkzeug.utils import secure_filename
from io import BytesIO

ZIP_PASSWORD = b'pixoprotect'

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

        def extract_zip(zip_file, destination):
            try:
                with zipfile.ZipFile(BytesIO(zip_file.read())) as zf:
                    if zf.namelist()[0].startswith('__MACOSX'):
                        return False, "MacOS hidden files detected. Skipping extraction."
                    zf.extractall(path=destination, pwd=app.config["ZIP_PASSWORD"])
                return True, "Archive extracted successfully."
            except zipfile.BadZipFile:
                return False, "Not a valid zip file."
            except RuntimeError:
                os.remove(os.path.join(destination, zip_file.filename))
                return False, "Incorrect password or unprotected zip file. File deleted."
            except Exception as e:
                return False, f"Error extracting zip: {str(e)}"

        uploaded_filenames = []
        for file in files:
            if file and file.filename:
                filename = secure_filename(file.filename)
                if filename.lower().endswith('.zip'):
                    success, message = extract_zip(file, app.config["UPLOAD_FOLDER"])
                    if success:
                        uploaded_filenames.append(f"{filename} (extracted)")
                    else:
                        if "Incorrect password or unprotected zip file" in message:
                            uploaded_filenames.append(f"{filename} (deleted: {message})")
                        uploaded_filenames.append(f"{filename} (not extracted: {message})")
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
            # Launch the main script (main.py) in the project directory.
            result = subprocess.run(
                ["python", "main.py"],
                cwd=app.config["PROJECT_DIR"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            output = result.stdout
            # Return the output in a preformatted block with a Back button.
            output_template = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Main Script Output</title>
            </head>
            <body>
                <h2>Main Script Output:</h2>
                <pre>{{ output }}</pre>
                <button onclick="window.location.href='/?token={{ token }}'">Back</button>
            </body>
            </html>
            """
            return render_template_string(output_template, output=output, token=upload_token)
        except Exception as e:
            return f"Error running main script: {e}", 500

    return app

def run_server(upload_token: str, upload_folder: str, project_dir: str, host="127.0.0.1", port=5000):
    """
    Create the Flask app with the given token, upload folder, and project folder.
    Then, run the app.
    """
    app = create_app(upload_token, upload_folder, project_dir)
    print(f"Starting web interface on http://{host}:{port}?token={upload_token}")
    print("Press Ctrl+C to stop the server.")
    app.run(host=host, port=port, debug=False)
