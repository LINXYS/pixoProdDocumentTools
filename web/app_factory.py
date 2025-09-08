import os
from flask import Flask, request, render_template_string, abort
from werkzeug.utils import secure_filename
from .templates import HTML_TEMPLATE, UPLOAD_RESULT_TEMPLATE
from .utils import extract_archive, run_script

def create_app(upload_token: str, project_dir: str) -> Flask:
    app = Flask(__name__)
    app.config["UPLOAD_FOLDER"] = os.path.join(project_dir, "files")
    app.config["PROJECT_DIR"] = project_dir
    app.config["ZIP_PASSWORD"] = b'pixoprotect'

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

        files = request.files.getlist("upload_files")
        if not files or all(file.filename == "" for file in files):
            return "No selected file", 400

        uploaded_filenames = []
        for file in files:
            if file and file.filename:
                filename = secure_filename(file.filename)
                if filename.lower().endswith(('.zip', '.rar')):
                    archive_type = 'zip' if filename.lower().endswith('.zip') else 'rar'
                    success, message = extract_archive(file, app.config["UPLOAD_FOLDER"], archive_type, app.config["ZIP_PASSWORD"])
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

        return run_script(app.config["PROJECT_DIR"], upload_token)

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        token = request.form.get("token", "")
        if token != upload_token:
            return abort(403, description="Invalid or missing token")
        shutdown_function = request.environ.get("werkzeug.server.shutdown")
        if shutdown_function is None:
            return "Not running with the Werkzeug Server", 500
        shutdown_function()
        return "Server shutting down..."

    return app
