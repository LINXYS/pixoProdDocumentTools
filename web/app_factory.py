import os
import yaml
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

        # Load existing config.yaml (if any) to pre-fill form fields
        config_path = os.path.join(app.config["PROJECT_DIR"], "config.yaml")
        ingestion_cfg = {}
        remote_source = {}
        urls_text = ""
        sitemap_url = ""
        css_selector = ""
        remote_protocol = ""
        remote_host = ""
        remote_port = ""
        remote_username = ""
        remote_path = ""
        remote_passive = False
        remote_recursive = True
        force_fresh_run = False

        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                ingestion_cfg = cfg.get("ingestion") or {}
                force_fresh_run = bool(ingestion_cfg.get("force_fresh_run", False))
                urls = ingestion_cfg.get("urls") or []
                if isinstance(urls, list):
                    urls_text = "\n".join(urls)
                sitemap_url = ingestion_cfg.get("sitemap_url") or ""
                css_selector = ingestion_cfg.get("css_selector") or ""
                remote_source = ingestion_cfg.get("remote_source") or {}
                remote_protocol = (remote_source.get("protocol") or "").lower()
                remote_host = remote_source.get("host") or ""
                remote_port = str(remote_source.get("port") or "")
                remote_username = remote_source.get("username") or ""
                remote_path = remote_source.get("remote_path") or ""
                remote_passive = bool(remote_source.get("passive", False))
                remote_recursive = bool(remote_source.get("recursive", True))
            except Exception:
                # Fail silently for UI; detailed errors will surface when running the script
                pass

        return render_template_string(
            HTML_TEMPLATE,
            token=upload_token,
            urls_text=urls_text,
            sitemap_url=sitemap_url,
            css_selector=css_selector,
            force_fresh_run=force_fresh_run,
            remote_protocol=remote_protocol,
            remote_host=remote_host,
            remote_port=remote_port,
            remote_username=remote_username,
            remote_path=remote_path,
            remote_passive=remote_passive,
            remote_recursive=remote_recursive,
        )

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
        force_fresh_run_flag = bool(request.form.get("force_fresh_run"))

        # Remote source (FTP/FTPS/SFTP) inputs
        remote_protocol = (request.form.get("remote_protocol", "") or "").strip().lower()
        remote_host = (request.form.get("remote_host", "") or "").strip()
        remote_port = (request.form.get("remote_port", "") or "").strip()
        remote_username = (request.form.get("remote_username", "") or "").strip()
        remote_password = request.form.get("remote_password", "") or ""
        remote_path = (request.form.get("remote_path", "") or "").strip()
        remote_passive = bool(request.form.get("remote_passive"))
        remote_recursive = bool(request.form.get("remote_recursive"))

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

        try:
            os.makedirs(app.config["PROJECT_DIR"], exist_ok=True)
            config_path = os.path.join(app.config["PROJECT_DIR"], "config.yaml")

            # Load existing config (if any) so we don't clobber unrelated keys
            existing_cfg = {}
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        existing_cfg = yaml.safe_load(f) or {}
                except Exception:
                    existing_cfg = {}

            ingestion_cfg = existing_cfg.get("ingestion") or {}

            # Website ingestion settings
            ingestion_cfg["urls"] = urls_list
            ingestion_cfg["sitemap_url"] = sitemap_url.strip() or None
            ingestion_cfg["css_selector"] = css_selector.strip() or None

            # Remote source settings
            existing_remote = ingestion_cfg.get("remote_source") or {}

            # Only configure remote_source if protocol and host are provided
            if remote_protocol and remote_host:
                remote_cfg = dict(existing_remote)  # start from existing so we can preserve password
                remote_cfg["protocol"] = remote_protocol
                remote_cfg["host"] = remote_host
                # Port: user-provided or protocol default
                if remote_port:
                    try:
                        remote_cfg["port"] = int(remote_port)
                    except ValueError:
                        remote_cfg["port"] = None
                else:
                    remote_cfg["port"] = 21 if remote_protocol in ("ftp", "ftps") else 22
                remote_cfg["username"] = remote_username or None
                # Password: only overwrite if user entered something
                if remote_password:
                    remote_cfg["password"] = remote_password
                else:
                    # keep existing password if present; otherwise leave as-is/None
                    if "password" not in remote_cfg:
                        remote_cfg["password"] = None
                remote_cfg["remote_path"] = remote_path or "."
                remote_cfg["passive"] = bool(remote_passive) if remote_protocol in ("ftp", "ftps") else None
                remote_cfg["recursive"] = bool(remote_recursive)

                ingestion_cfg["remote_source"] = remote_cfg
            else:
                # Clear remote_source if incomplete
                ingestion_cfg["remote_source"] = None

            ingestion_cfg["force_fresh_run"] = bool(force_fresh_run_flag)

            existing_cfg["ingestion"] = ingestion_cfg

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(existing_cfg, f, sort_keys=False, allow_unicode=True)
        except Exception as e:
            return f"Failed to write config.yaml: {e}", 500

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
