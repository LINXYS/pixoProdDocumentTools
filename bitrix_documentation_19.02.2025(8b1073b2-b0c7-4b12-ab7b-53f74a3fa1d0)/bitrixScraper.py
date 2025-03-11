import os
import json
from typing import List
from langchain.schema import Document
from bs4 import BeautifulSoup
import requests
from fake_useragent import UserAgent

from markdownify import markdownify
from tqdm import tqdm

from utils import save_document


class BitrixDocProcessor:
    def __init__(self, links_file: str, output_folder: str):
        self.links_file = links_file
        self.output_folder = output_folder
        self.user_agent = UserAgent()

    def process_documents(self):
        links = self._read_links()
        for link in tqdm(links, desc="Processing documents"):
            content, title = self._fetch_content(link)
            if content and title:
                doc = self._create_document(content, link, title)
                save_document(doc)

    def _read_links(self) -> List[str]:
        with open(self.links_file, 'r') as file:
            return [line.strip() for line in file if line.strip()]

    def _fetch_content(self, url: str) -> str:
        headers = {'User-Agent': self.user_agent.random}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')
            content = soup.find(class_='bx-help-post-text-block')
            title = soup.title.string if soup.title else ""
            return str(content) if content else "", title
        return "", ""

    def _create_document(self, content: str, url: str, title: str) -> Document:
        text = self._clean_text(markdownify(content))
        return Document(page_content=text, metadata={"source": url, "filetype": "text/html", "name": title})

    def _clean_text(self, text: str) -> str:
        unwanted_patterns = [
            "War diese Information hilfreich?",
            "Assistenz von Integrationsspezialisten",
            "Nicht das, wonach ich suche.",
            "Kompliziert und unverständlich formuliert.",
            "Die Information ist veraltet.",
            "Zu kurz, ich benötige mehr Informationen.",
            "Mir gefällt nicht, wie das Tool funktioniert.",
            "Sie haben noch keinen Account?",
            "Zu Bitrix24 wechseln",
            "Autor:",
            "Zuletzt aktualisiert am",
            '{"@context":"http:\/\/schema.org",',
            'Sehen Sie sich unsere Videos an',
            "Bitrix24 Webinaraufnahmen und Videos"
        ]

        lines = text.split('\n')
        cleaned_lines = [line for line in lines if not any(pattern in line for pattern in unwanted_patterns)]
        return '\n'.join(cleaned_lines).strip()


if __name__ == "__main__":
    processor = BitrixDocProcessor("links.txt", "files")
    processor.process_documents()
