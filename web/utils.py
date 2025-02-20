import os
import subprocess
import platform
from io import BytesIO
import pyzipper
import rarfile
from flask import stream_with_context

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
            script_name = "schedule_ingestion.bat"
            script_path = os.path.join(project_root, test_folder, script_name)
            command = f'cmd /c ""{script_path}""'
        else:
            script_name = "schedule_ingestion.sh"
            script_path = os.path.join(project_root, test_folder, script_name)
            command = f'/bin/bash "{script_path}"'

        print("Running script:", script_path)

        environment = os.environ.copy()
        process = None

        def generate():
            yield "<html><head><title>Main Script Output</title></head><body><pre>\n"
            nonlocal process
            process = subprocess.Popen(
                    command,
                    cwd=os.path.dirname(script_path),
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=0,
                    shell=True
            )
            while True:
                character = process.stdout.read(1)
                if not character and process.poll() is not None:
                    break
                if character:
                    yield character
            yield "</pre>\n"
            yield f"<button onclick=\"window.location.href='/?token={upload_token}'\">Back</button>\n"
            yield "</body></html>\n"

        return stream_with_context(generate())
    except Exception as exception:
        return f"Error running schedule_ingestion script: {str(exception)}", 500
