import os
import pty
import subprocess
import platform
from io import BytesIO
import pyzipper
import rarfile
import select
from flask import stream_with_context, Response


def extract_archive(archive_file, destination, archive_type, zip_password):
    try:
        if archive_type == 'zip':
            archive_data = BytesIO(archive_file.read())
            with pyzipper.AESZipFile(archive_data) as zip_file:
                if zip_file.namelist()[0].startswith('__MACOSX'):
                    return False, "MacOS hidden files detected. Skipping extraction."
                try:
                    zip_file.pwd = zip_password
                    zip_file.extractall(path=destination)
                except RuntimeError:
                    return False, "Incorrect password for zip file. File deleted."
            return True, "Archive extracted successfully."
        elif archive_type == 'rar':
            archive_data = BytesIO(archive_file.read())
            with rarfile.RarFile(archive_data) as rar_file:
                try:
                    rar_file.extractall(path=destination, pwd=zip_password)
                except rarfile.BadRarFile:
                    return False, "Incorrect password for rar file. File deleted."
            return True, "Archive extracted successfully."
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
            # Windows: regular pipe streaming works fine
            script_name = "schedule_ingestion.bat"
            script_path = os.path.join(project_root, test_folder, script_name)
            cmd = ["cmd", "/c", script_path]

            def generate_win():
                yield "<html><head><title>Main Script Output</title></head><body><pre>\n"
                env = os.environ.copy()
                # text=True + bufsize=1 => line-buffered in Python when stdout is a pipe
                with subprocess.Popen(
                    cmd,
                    cwd=os.path.dirname(script_path),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
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

        # POSIX (Linux/macOS): run under a PTY to force line-buffering in the child
        script_name = "schedule_ingestion.sh"
        script_path = os.path.join(project_root, test_folder, script_name)
        cmd = ["/bin/bash", script_path]

        def generate_posix():
            yield "<html><head><title>Main Script Output</title></head><body><pre>\n"
            env = os.environ.copy()
            # Helps if the called script runs Python children
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
                # Parent should only read from master
                try:
                    os.close(slave_fd)
                except Exception:
                    pass

            try:
                # Read incrementally without blocking
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
                    # If the process exited, drain remaining bytes and break
                    if proc.poll() is not None:
                        # Drain anything left
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
                # Ensure process is reaped
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass

            yield "</pre>\n"
            yield f"<button onclick=\"window.location.href='/?token={upload_token}'\">Back</button>\n"
            yield "</body></html>\n"

        # Return a streaming response and tell common proxies not to buffer it
        return Response(
            stream_with_context(generate_posix()),
            mimetype="text/html",
            headers={
                "Cache-Control": "no-cache",
                # Nginx honors this to disable per-request proxy buffering
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as exception:
        return f"Error running schedule_ingestion script: {str(exception)}", 500
