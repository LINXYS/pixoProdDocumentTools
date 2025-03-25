import requests
import xml.etree.ElementTree as ET
from typing import Iterator

from langchain.docstore.document import Document
from langchain.document_loaders.base import BaseLoader


class KRBXmLLoader(BaseLoader):
    """
    Loader to fetch and parse an XML feed, converting each <item> into a langchain Document.

    For each <item>, the following are extracted:
      - page_content: A concatenation of title, brand, description, link, price, and shipping details.
      - metadata: A dict with:
          * source: the value from the link element.
          * product_type: a list of product types parsed from the <g:product_type> element.
    """

    def __init__(self, url: str):
        """
        Initialize the loader with the URL to the XML.

        Args:
            url (str): The URL where the XML can be fetched (e.g. "https://test.com").
        """
        self.url = url
        # Define the XML namespaces for proper lookup.
        self.namespaces = {
            "g": "http://base.google.com/ns/1.0",
            "atom": "http://www.w3.org/2005/Atom",
        }

    def lazy_load(self) -> Iterator[Document]:
        try:
            response = requests.get(self.url)
            response.raise_for_status()
        except Exception as e:
            raise ValueError(f"Failed to fetch XML from {self.url}: {e}")

        try:
            root = ET.fromstring(response.text)
        except Exception as e:
            raise ValueError(f"Failed to parse XML: {e}")

        # Iterate through each <item> element.
        for item in root.findall(".//item"):
            # Retrieve fields gracefully. Use default empty string if field is missing.
            title = item.findtext("title", default="").strip()
            description = item.findtext("description", default="").strip()
            link = item.findtext("link", default="").strip()

            # Extract the price using namespace.
            price = item.findtext("g:price", default="", namespaces=self.namespaces).strip()

            # Extract shipping information.
            shipping_elem = item.find("g:shipping", namespaces=self.namespaces)
            shipping_price = ""
            shipping_country = ""
            if shipping_elem is not None:
                shipping_price = shipping_elem.findtext("g:price", default="", namespaces=self.namespaces).strip()
                shipping_country = shipping_elem.findtext("g:country", default="", namespaces=self.namespaces).strip()

            # Extract brand information.
            brand = item.findtext("g:brand", default="", namespaces=self.namespaces).strip()

            # Parse product_type and split it into an array by " > "
            raw_product_type = item.findtext("g:product_type", default="", namespaces=self.namespaces).strip()
            product_type_array = (
                [pt.strip() for pt in raw_product_type.split(" > ") if pt.strip()]
                if raw_product_type
                else []
            )

            # Construct the page_content with desired fields.
            page_content = (
                f"Name: {title}\n"
                f"Marke: {brand}\n"
                f"Beschreibung: {description}\n"
                f"Link: {link}\n"
                f"Preis: {price}\n"
                f"Versand: {shipping_price} im Land {shipping_country}"
            )
            # Metadata includes the source (the link) and product_type as an array.
            metadata = {"source": link, "product_type": product_type_array}

            yield Document(page_content=page_content, metadata=metadata)
