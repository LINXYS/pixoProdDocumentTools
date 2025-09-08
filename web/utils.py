import os
import subprocess
import platform
from io import BytesIO
import pyzipper
import rarfile
from flask import stream_with_context, Response


def extract_archive(archive_file, destination, archive_type, zip_password):
    try:
        if archive_type == 'zip':
            archive_data = BytesIO(archive_file.read())
            with pyzipper.AESZipFile(archive_data) as zip_file:
                names = zip_file.namelist()
                if names and any(n.startswith('__MACOSX') for n in names):
                    return False, "MacOS hidden files detected. Skipping extraction."
                try:
                    # pyzipper expects bytes for the password
                    zip_file.pwd = zip_password.encode('utf-8') if zip_password else None
                    zip_file.extractall(path=destination)
                except RuntimeError:
                    return False, "Incorrect password for zip file. File deleted."
            return True, "Archive extracted successfully."

        elif archive_type == 'rar':
            archive_data = BytesIO(archive_file.read())
            # rarfile prefers fileobj= for in-memory data
            with rarfile.RarFile(fileobj=archive_data) as rf:
                try:
                    rf.extractall(path=destination, pwd=zip_password)
                except rarfile.BadRarFile:
                    return False, "Incorrect password for rar file. File deleted."
                except rarfile.NeedFirstVolume:
                    return False, "Multi-part RAR not supported (need first volume)."
                except rarfile.RarCannotExec as e:
                    return False, f"RAR backend missing on this system: {e}"
            return True, "Archive extracted successfully."

        else:
            return False, f"Unsupported archive type: {archive_type}"

    except (pyzipper.BadZipFile, rarfile.BadRarFile):
        return False, f"Not a valid {archive_type} file."
    except Exception as exception:
        return False, f"Error extracting {archive_type}: {str(exception)}"


def run_script(project_directory, upload_token):
    try:
        web_directory = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(web_directory, os.pardir))
        test_folder = project_directory

        if platform.system() == "Windows":
            # Windows: stream via pipes (no PTY available)
            script_name = "schedule_ingestion.bat"
            script_path = os.path.join(project_root, test_folder, script_name)
            cmd = ["cmd", "/c", script_path]

            def generate_win():
                yield "<html><head><title>Main Script Output</title></head><body><pre>\n"
                env = os.environ.copy()
                with subprocess.Popen(
                    cmd,
                    cwd=os.path.dirname(script_path),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,          # decode to str
                    bufsize=1,          # line-buffered in Python
                ) as proc:
                    for line in proc.stdout:
                        yield line
                    proc.wait()
                yield "</pre>\n"
                yield f"<button onclick=\"window.location.href='/?token={upload_token}'\">Back</button>\n"
                yield "</body></html>\n"

            return Response(
                stream_with_context(generate_win()),
                mimetype="text/html",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # POSIX (Linux/macOS): use a PTY to coax line-buffering from children
        # Import pty/select only here so Windows never imports Unix-only modules.
        import pty
        import select

        script_name = "schedule_ingestion.sh"
        script_path = os.path.join(project_root, test_folder, script_name)
        cmd = ["/bin/bash", script_path]

        def generate_posix():
            yield "<html><head><title>Main Script Output</title></head><body><pre>\n"
            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")

            master_fd, slave_fd = pty.openpty()
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=os.path.dirname(script_path),
                    env=env,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True,
                )
            finally:
                try:
                    os.close(slave_fd)
                except Exception:
                    pass

            try:
                while True:
                    r, _, _ = select.select([master_fd], [], [], 0.1)
                    if master_fd in r:
                        try:
                            chunk = os.read(master_fd, 4096)
                        except OSError:
                            break
                        if not chunk:
                            break
                        yield chunk.decode("utf-8", errors="replace")
                    if proc.poll() is not None:
                        # Drain any remaining bytes
                        while True:
                            try:
                                chunk = os.read(master_fd, 4096)
                                if not chunk:
                                    break
                                yield chunk.decode("utf-8", errors="replace")
                            except OSError:
                                break
                        break
            finally:
                try:
                    os.close(master_fd)
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass

            yield "</pre>\n"
            yield f"<button onclick=\"window.location.href='/?token={upload_token}'\">Back</button>\n"
            yield "</body></html>\n"

        return Response(
            stream_with_context(generate_posix()),
            mimetype="text/html",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as exception:
        return f"Error running schedule_ingestion script: {str(exception)}", 500
