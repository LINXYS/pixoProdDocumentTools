import json
import os
from urllib.parse import urlparse

from langchain_core.documents import Document


def save_document(doc: Document, output_folder: str = "files", filename: str = None):
    if not filename:
        filename = generate_filename(doc.metadata['source'])

    # check if output file already ends with .pixodoc
    if not filename.endswith(".pixodoc"):
        filename += ".pixodoc"

    filepath = os.path.join(output_folder, f"{filename}")

    with open(filepath, 'w', encoding='utf-8') as file:
        json.dump({
            "page_content": doc.page_content,
            "metadata": doc.metadata
        }, file, ensure_ascii=False, indent=2)


def generate_filename(url: str) -> str:
    parsed_url = urlparse(url)
    path = parsed_url.path.strip('/')
    return '_'.join(path.split('/')[-2:])
