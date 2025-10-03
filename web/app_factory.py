import json
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

        # Parse inputs
        raw_urls = request.form.get("urls", "") or ""
        sitemap_url = request.form.get("sitemap_url", "") or ""
        css_selector = request.form.get("css_selector", "") or ""

        def normalize_urls(text):
            # newline separated is primary; also allow commas within lines
            items = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                # further split by comma if present
                for part in line.split(","):
                    u = part.strip()
                    if not u:
                        continue
                    items.append(u)
            # dedupe while preserving order
            seen = set()
            ordered = []
            for u in items:
                if u not in seen:
                    seen.add(u)
                    ordered.append(u)
            return ordered

        urls_list = normalize_urls(raw_urls)

        # Always write urls.json (even if empty), into the project directory
        urls_payload = {
            "urls": urls_list,
            "sitemap_url": sitemap_url.strip(),
            "css_selector": css_selector.strip(),
        }
        try:
            os.makedirs(app.config["PROJECT_DIR"], exist_ok=True)
            urls_path = os.path.join(app.config["PROJECT_DIR"], "urls.json")
            with open(urls_path, "w", encoding="utf-8") as f:
                json.dump(urls_payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Failed to write urls.json: {e}", 500

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
