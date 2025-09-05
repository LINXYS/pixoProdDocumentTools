import logging
import threading
import time
import re
from typing import List, Dict, Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException, NoSuchWindowException
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from markdownify import markdownify as md
from tqdm.contrib.concurrent import thread_map

from langchain.docstore.document import Document


# --- Utility functions ---

def replace_non_breaking_spaces(text: str) -> str:
    """Replace non-breaking spaces with a regular space."""
    return text.replace(u'\u00A0', ' ')


def sanitize_filename(filename: str) -> str:
    """Sanitize filename if needed (not used for file saving here)."""
    return re.sub(r'[\\/*?:"<>|]', '_', filename)


# --- Main class combining all functionality ---

class ZohoAllInOne:
    """
    This class merges the Zoho functionalities:
      1. Scrapes the Zoho KB homepage for top-level projects.
      2. Recursively collects article links from project pages.
      3. Parses each article page into Markdown (via LangChain Document objects).

    The final output is:
      - A list of LangChain Document objects,
      - A list of project dictionaries,
      - A list of all unique links visited.

    No files are written; everything is maintained in memory.
    """

    def __init__(self, base_url: str = "https://help.zoho.com/portal/en/kb", max_workers: int = 4, debug: bool = False):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG if debug else logging.INFO)
        self.base_url = base_url
        self.max_workers = max_workers
        self.debug = debug

        self.all_projects: List[Dict[str, Any]] = []  # Project data, each with its article links.
        self.documents: List[Document] = []  # LangChain Document objects (parsed articles).
        self.visited_links = set()  # All links encountered during scraping.

    # --- Part 1: Scraping top-level projects ---

    def get_projects_from_kb(self, wait_time=20) -> List[Dict[str, Any]]:
        """
        Scrape the KB homepage to obtain a list of projects.
        Each project is a dictionary with keys: link, title, image_url, and an empty list for article links.
        """
        options = FirefoxOptions()
        options.add_argument("--headless")
        driver = webdriver.Firefox(options=options)
        try:
            driver.get(self.base_url)
            try:
                WebDriverWait(driver, wait_time).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "TitleContainer__gridView"))
                )
            except TimeoutException:
                self.logger.warning("Timeout waiting for project elements.")

            self._scroll_down(driver)
            projects = self._extract_project_data(driver)
            return projects
        finally:
            driver.quit()

    def _scroll_down(self, driver, pause_time=2):
        """Scroll the page until no more new content loads."""
        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(pause_time)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    def _extract_project_data(self, driver) -> List[Dict[str, Any]]:
        """
        Extract project details from the page.
        Each project includes link, title, image_url and an empty 'links' list for later article links.
        """
        max_retries = 3
        results = []
        for attempt in range(max_retries):
            try:
                containers = driver.find_elements(By.CLASS_NAME, 'TitleContainer__gridView')
                for container in containers:
                    # Extract title
                    title = None
                    try:
                        title_elem = container.find_element(By.CLASS_NAME, 'TitleContainer__title')
                        title = title_elem.text.strip()
                    except NoSuchElementException:
                        pass
                    # Extract image URL
                    image_url = None
                    try:
                        image_elem = container.find_element(By.CLASS_NAME, 'Avatar__img')
                        image_url = image_elem.get_attribute('src') or image_elem.get_attribute('data-src')
                    except NoSuchElementException:
                        pass
                    # Extract link
                    href = None
                    try:
                        link_elem = title_elem.find_element(By.TAG_NAME, 'a')
                        href = link_elem.get_attribute('href')
                        if href:
                            if href.startswith('/'):
                                href = self.base_url + href
                            elif not href.startswith('http'):
                                href = self.base_url + '/' + href
                    except (NoSuchElementException, AttributeError):
                        pass
                    results.append({
                        'link': href,
                        'title': title,
                        'image_url': image_url,
                        'links': []  # To be populated with article links
                    })
                    # DEBUG: If in debug mode, break only if this project has a valid link.
                    if self.debug and href:
                        self.logger.debug("DEBUG: Valid project found. Limited to one project for debugging.")
                        break
                break
            except StaleElementReferenceException:
                self.logger.warning(f"Stale element reference. Retrying project extraction (attempt {attempt + 1})")
                time.sleep(1)
                results = []
        return results

    # --- Part 2: Recursively collecting article links ---

    def get_sublinks_selenium(self, url: str, wait_time=20) -> List[str]:
        """
        Use Selenium to extract all valid links from the provided URL.
        """
        options = FirefoxOptions()
        options.add_argument("--headless")
        driver = webdriver.Firefox(options=options)
        try:
            driver.get(url)
            try:
                WebDriverWait(driver, wait_time).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "Link__link"))
                )
            except TimeoutException:
                self.logger.info(f"{url}: Timeout waiting for sublinks, proceeding with available content.")
            links = self._extract_links(driver, url)
            return links
        except NoSuchWindowException:
            self.logger.error(f"{url}: NoSuchWindowException encountered.")
            return []
        finally:
            driver.quit()

    def _extract_links(self, driver, current_url: str) -> List[str]:
        """
        Extract and clean links from the page.
        """
        max_retries = 3
        complete_links = []
        for attempt in range(max_retries):
            try:
                elements = driver.find_elements(By.TAG_NAME, 'a')
                for element in elements:
                    href = element.get_attribute('href')
                    if href:
                        if href.startswith('/'):
                            href = self.base_url + href
                        elif not href.startswith('http'):
                            href = self.base_url + '/' + href
                        if href.startswith(self.base_url):
                            complete_links.append(href)
                break
            except StaleElementReferenceException:
                self.logger.warning(
                    f"{current_url}: Stale element reference in link extraction (attempt {attempt + 1})")
                time.sleep(1)
                complete_links = []
        # DEBUG: Only use the first link for debugging if available.
        if self.debug and complete_links:
            self.logger.debug("DEBUG: Limited to one set of links for debugging.")
            return [complete_links[0]]
        return complete_links

    def scrape_links_recursive(self, url: str, project_data: Dict[str, Any]):
        """
        Recursively gather article links starting from the given URL.
        An article link is identified by containing '/articles/'.
        Non-article links are recursively processed.
        """
        current_links = self.get_sublinks_selenium(url)
        self.logger.info(f"Found {len(current_links)} links at {url}")
        for link in current_links:
            with threading.Lock():
                if link not in self.visited_links and "#" not in link and "mailto:" not in link:
                    self.visited_links.add(link)
                    if "/articles/" not in link:
                        self.scrape_links_recursive(link, project_data)
                    else:
                        project_data['links'].append(link)
            # DEBUG: Limit to processing one sublink for debugging.
            if self.debug:
                self.logger.debug("DEBUG: Limited to one sublink for debugging.")
                break

    # --- Part 3: Parsing article pages to create LangChain Documents ---

    class _ParserZoho:
        """
        Parser for Zoho article pages.

        Uses Selenium to load the page, converts the HTML to Markdown, cleans the text,
        and returns a LangChain Document.
        """

        def __init__(self, links: List[str], max_workers: int = 4, codelanguage=None,
                     waitForConditionValue: str = '//*[@id="article_TOC"]/p', debug: bool = False):
            self.links = links
            self.max_workers = max_workers
            self.codelanguage = codelanguage
            self.waitForConditionValue = waitForConditionValue
            self.debug = debug

        def parse(self) -> List[Document]:
            """
            Concurrently parse all article links and return a list of Document objects.
            """
            # DEBUG: Limit to first article link if debugging
            links_to_process = self.links[:1] if self.debug else self.links

            def process_wrapper(args):
                return self._process_link(*args)
            args_list = [(link, i) for i, link in enumerate(links_to_process)]
            docs = thread_map(process_wrapper, args_list, max_workers=self.max_workers, desc="Parsing Articles")
            return [doc for doc in docs if doc is not None]

        def _process_link(self, link: str, i: int, retry_count=0) -> Document:
            max_retries = 3
            try:
                options = FirefoxOptions()
                options.add_argument("--headless")
                driver = webdriver.Firefox(options=options)
                try:
                    driver.get(link)
                    # Wait for a condition that indicates the article is loaded.
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.XPATH, self.waitForConditionValue))
                    )
                except TimeoutException:
                    pass
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                driver.quit()

                # Extract article content from element with id "article_TOC"
                content_elements = soup.find_all(id="article_TOC")
                if not content_elements:
                    return None
                fullText = ''.join(str(e) for e in content_elements)
                htmlmd = md(fullText, autolinks=False, heading_style="ATX", code_language=self.codelanguage,
                            escape_asterisks=False, escape_underscores=False, strip=['img'])
                htmlmd = self.__remove_extra_newlines(htmlmd)
                if self.codelanguage is not None:
                    htmlmd = self.__remove_empty_lines_from_codeblocks(htmlmd)
                htmlmd = replace_non_breaking_spaces(htmlmd)
                htmlmd = self._cleanText(htmlmd)
                # Extract title if available; otherwise set a default.
                title_tag = soup.find("h1", {"data-id": "articleTitle"})
                title = title_tag.text.strip() if title_tag and title_tag.text.strip() else f"Article_{i + 1}"
                return Document(
                    page_content=htmlmd,
                    metadata={"source": link, "title": title}
                )
            except Exception as e:
                logging.error(f"Error processing article {link}: {e}")
                if retry_count < max_retries:
                    return self._process_link(link, i, retry_count + 1)
                return None

        def _cleanText(self, text: str) -> str:
            unwanted_patterns = [".sty__", "We hope that "]
            lines = text.split('\n')
            cleaned_lines = [line for line in lines if not any(line.startswith(patt) for patt in unwanted_patterns)]
            return '\n'.join(cleaned_lines).strip()

        def __remove_empty_lines_from_codeblocks(self, markdown_text: str) -> str:
            code_block_pattern = r'```.*?```'

            def remove_empty_lines(block):
                return '\n'.join(line for line in block.splitlines() if line.strip())

            code_blocks = re.findall(code_block_pattern, markdown_text, flags=re.DOTALL)
            for block in code_blocks:
                cleaned = remove_empty_lines(block)
                markdown_text = markdown_text.replace(block, cleaned)
            return markdown_text

        def __remove_extra_newlines(self, markdown_text: str) -> str:
            lines = markdown_text.split('\n')
            cleaned_lines = [line for line in lines if line.strip() != '']
            return '\n'.join(cleaned_lines)

    # --- Main pipeline execution ---

    def run(self) -> (List[Document], List[Dict[str, Any]], List[str]):
        """
        Runs the complete pipeline:
          1. Scrape top-level projects.
          2. For each project, recursively gather article links.
          3. For each project, parse each article into a Document.
          4. Return a tuple: (list_of_documents, list_of_projects, list_of_all_unique_links)
        """
        # 1. Get top-level projects.
        projects = self.get_projects_from_kb()
        self.visited_links.add(self.base_url)

        # 2. Collect article links for each project.
        for proj in projects:
            project_link = proj.get('link')
            if not project_link:
                continue
            self.scrape_links_recursive(project_link, proj)
            self.all_projects.append(proj)
            # DEBUG: Process only one project for debugging
            if self.debug:
                self.logger.debug("DEBUG: Limited to one project for debugging.")
                break

        # 3. Parse articles from each project.
        for proj in self.all_projects:
            article_links = proj.get("links", [])
            if not article_links:
                self.logger.info(f"No article links for project {proj.get('title')}")
                continue
            parser = self._ParserZoho(links=article_links, max_workers=self.max_workers, codelanguage=None, debug=self.debug)
            docs = parser.parse()
            self.documents.extend(docs)
            # DEBUG: Process only one project for article parsing
            if self.debug:
                self.logger.debug("DEBUG: Limited to parsing articles for one project.")
                break

        return self.documents, self.all_projects, list(self.visited_links)


# --- Example usage ---

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Enable debug mode to limit execution to one project, one set of links, etc.
    zoho = ZohoAllInOne(debug=True)
    docs, projects, links = zoho.run()

    print("==== LANGCHAIN DOCUMENTS ====")
    print(f"Total documents: {len(docs)}")
    for doc in docs:
        print(f"- Title: {doc.metadata.get('title')} | Source: {doc.metadata.get('source')}")

    print("\n==== PROJECTS ====")
    print(f"Total projects: {len(projects)}")
    for proj in projects:
        print(f"- {proj.get('title')} (Articles: {len(proj.get('links', []))})")

    print("\n==== ALL UNIQUE LINKS ====")
    print(f"Total unique links: {len(links)}")
    for link in links:
        print(f"- {link}")
