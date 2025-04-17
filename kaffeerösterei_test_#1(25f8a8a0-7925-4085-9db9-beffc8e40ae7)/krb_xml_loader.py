#!/usr/bin/env python
"""
Test runner for the Berliner‑Kaffeerösterei XML loader
-----------------------------------------------------

• .env must contain   KRB_URL=https://…/feed.xml
• Prints each document’s page_content and metadata.
"""

from __future__ import annotations

import os
import textwrap
import xml.etree.ElementTree as ET
from typing import Dict, Iterator, List, Optional

import requests
from dotenv import load_dotenv
from langchain.docstore.document import Document
from langchain.document_loaders.base import BaseLoader


# ====================================================================== #
#   L O A D E R                                                          #
# ====================================================================== #
class KRBXMLLoader(BaseLoader):
    """Transform the shop XML feed into langchain Documents."""

    # ------------------------------------------------------------------ #
    # 1. Mapping tables                                                  #
    # ------------------------------------------------------------------ #

    # Human‑readable custom fields → label
    TEXT_FIELD_LABELS: Dict[str, str] = {
        # generic
        "bisgSubline": "Produktlinie",
        "bisgSalesName": "Produktbezeichnung",
        "bisgOrigin": "Ursprung",
        "bisgManufacturer": "Hersteller",
        "bisgFirstplacer": "Inverkehrbringer",
        # categories
        "bisgCategoryTeaLabel": "Tee‑Kategorie",
        "bisgCategoryCoffeeLabel": "Kaffee‑Kategorie",
        # preparation
        "bisgDosage": "Dosierung",
        "bisgPreparationSteeptime": "Ziehzeit",
        "bisgPreparationTemperature": "Wassertemperatur",
        "bisgCoffeeBestSuitable": "Empfohlene Zubereitung",
        # coffee origin / processing
        "bisgCoffeeOriginType": "Herkunftstyp",
        "bisgCoffeeProcessing": "Aufbereitung",
        "bisgCoffeeQuality": "Qualität",
        "bisgCoffeeRegion": "Region",
        "bisgCoffeeSoil": "Boden",
        "bisgCoffeeVariety": "Varietät",
        "bisgCoffeePlantationName": "Plantagenname",
        # bio / certification
        "bisgBioOrigin": "Bio‑Herkunft",
        "bisgOekoKontrollstelle": "Öko‑Kontrollstelle",
        # ingredients & warnings
        "bisgNwZutaten": "Zutaten",
        "bisgWarnhinweise": "Warnhinweise",
        # finishing ingredients
        "bisgFinishingIngredient1": "Veredelungszutat 1",
        "bisgFinishingIngredient2": "Veredelungszutat 2",
        # flavour labels (first two each cluster)
        "bisgFlavorNutLabel_1": "Aroma (Nuss) 1",
        "bisgFlavorNutLabel_2": "Aroma (Nuss) 2",
        "bisgFlavorSpiceLabel_1": "Aroma (Gewürz) 1",
        "bisgFlavorSweetLabel_1": "Aroma (Süße) 1",
        "bisgFlavorToastLabel_1": "Aroma (Röstung) 1",
        "bisgFlavorToastLabel_2": "Aroma (Röstung) 2",
    }

    # Coffee SCP ratings → label
    RATING_FIELDS: Dict[str, str] = {
        "bisgCoffeeAcidity": "Säure",
        "bisgCoffeeAftertaste": "Nachgeschmack",
        "bisgCoffeeBalance": "Balance",
        "bisgCoffeeBody": "Körper",
        "bisgCoffeeBouquet": "Bouquet",
        "bisgCoffeeComplexity": "Komplexität",
        "bisgCoffeeFlavors": "Aromenvielfalt",
        "bisgCoffeeFruitiness": "Fruchtigkeit",
        "bisgCoffeeHarmony": "Harmonie",
        "bisgCoffeeSweetness": "Süße",
    }

    # customField → (metadata_key, human label)
    BOOLEAN_FLAGS: Dict[str, tuple[str, str]] = {
        "bisgFilterVegan": ("is_vegan", "Vegan"),
        "bisgFilterVegetarisch": ("is_vegetarian", "Vegetarisch"),
        "bisgFilterGlutenfrei": ("is_gluten_free", "Glutenfrei"),
        "bisgFilterLaktosefrei": ("is_lactose_free", "Laktosefrei"),
        "bisgFilterPalmFatFree": ("is_palm_oil_free", "Palmölfrei"),
        "bisgFilterBiocert": ("is_bio_certified", "Bio"),
    }

    # Parsed only into metadata (floats)
    NUMERIC_FIELDS: Dict[str, str] = {
        # nutrition
        "bisgNwBwKcal": "kcal_per_100",
        "bisgNwBwKj": "kj_per_100",
        "bisgNwFett": "fat_g_per_100",
        "bisgNwGesFett": "sat_fat_g_per_100",
        "bisgNwKohlenhydrate": "carbs_g_per_100",
        "bisgNwZucker": "sugar_g_per_100",
        "bisgNwEiweiss": "protein_g_per_100",
        "bisgNwSalz": "salt_g_per_100",
        # misc
        "bisgCoffeeHeightMin": "height_min",
        "bisgCoffeeHeightMax": "height_max",
        "bisgCoffeeScaeRating": "scae_score",
        "bisgCoffeeAmount": "coffee_amount_g",
        "bisgGewichtEinzel": "gewicht_einzel_kg",
        "bisgMeltingTemperature": "melting_temp_c",
    }

    # Allergen flags go to metadata only, NOT to page_content
    ALLERGEN_PREFIXES = ("bisgAllergene", "bisgAllergens")

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _to_bool(text: str) -> bool:
        return str(text).strip().lower() in {"1", "true", "yes", "ja"}

    @staticmethod
    def _to_float(text: str) -> Optional[float]:
        try:
            return float(str(text).replace(",", "."))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _price_missing(price_raw: str) -> bool:
        """True if price is '', '0', '0.00 EUR', '0,00 EUR', …"""
        if not price_raw.strip():
            return True
        num_part = price_raw.split()[0].replace(",", ".")
        try:
            return float(num_part) <= 0
        except ValueError:
            return True

    # ------------------------------------------------------------------ #
    # Constructor                                                        #
    # ------------------------------------------------------------------ #

    def __init__(self, url: str):
        self.url = url
        self.ns = {"g": "http://base.google.com/ns/1.0"}

    # ------------------------------------------------------------------ #
    # Loader core                                                         #
    # ------------------------------------------------------------------ #

    def lazy_load(self) -> Iterator[Document]:
        xml = requests.get(self.url, timeout=30)
        xml.raise_for_status()
        root = ET.fromstring(xml.text)

        for item in root.findall(".//item"):
            # ---------------- basic google tags --------------------
            title = item.findtext("title", default="").strip()
            description = item.findtext("description", default="").strip()
            link = item.findtext("link", default="").strip()
            brand = item.findtext("g:brand", default="", namespaces=self.ns).strip()

            price_raw = item.findtext("g:price", default="", namespaces=self.ns).strip()
            price_val = self._to_float(price_raw.split()[0]) if price_raw else None

            ship_elem = item.find("g:shipping", namespaces=self.ns)
            ship_price_raw = ship_country = ""
            ship_price_val = None
            if ship_elem is not None:
                ship_price_raw = ship_elem.findtext("g:price", default="", namespaces=self.ns).strip()
                ship_country = ship_elem.findtext("g:country", default="", namespaces=self.ns).strip()
                ship_price_val = self._to_float(ship_price_raw.split()[0]) if ship_price_raw else None

            raw_product_type = item.findtext("g:product_type", default="", namespaces=self.ns).strip()
            product_types = [pt.strip() for pt in raw_product_type.split(" > ") if pt.strip()]

            # ---------------- metadata -----------------------------
            meta: Dict[str, object] = {"source": link, "product_type": product_types}
            if price_val not in (None, 0.0):
                meta["price_eur"] = price_val
            if ship_price_val not in (None, 0.0):
                meta["shipping_eur"] = ship_price_val
            if ship_country:
                meta["shipping_country"] = ship_country

            # ---------------- attribute <attribute> ---------------
            attr_lines = [
                f"{a.attrib.get('name', '').strip()}: {(a.text or '').strip()}"
                for a in item.findall("./attributes/attribute")
                if (a.text or "").strip()
            ]

            # ---------------- custom fields -----------------------
            text_lines: List[str] = []
            nutr: Dict[str, Optional[float]] = {}
            nutrition_unit = "g"

            for cf in item.findall("./customFields/customField"):
                name = cf.attrib.get("name", "").strip()
                value = (cf.text or "").strip()
                if not value:
                    continue

                if name.startswith(self.ALLERGEN_PREFIXES):
                    meta[name] = self._to_bool(value)
                    continue

                if name in self.BOOLEAN_FLAGS:
                    key, label = self.BOOLEAN_FLAGS[name]
                    val_bool = self._to_bool(value)
                    meta[key] = val_bool
                    if val_bool:
                        text_lines.append(f"{label}: Ja")
                    continue

                if name in self.TEXT_FIELD_LABELS:
                    text_lines.append(f"{self.TEXT_FIELD_LABELS[name]}: {value}")
                    continue

                if name in self.RATING_FIELDS:
                    rating = self._to_float(value)
                    if rating is not None:
                        text_lines.append(f"{self.RATING_FIELDS[name]}: {int(rating)}/5")
                        meta[self.RATING_FIELDS[name].lower() + "_rating"] = rating
                    continue

                if name in self.NUMERIC_FIELDS:
                    num_key = self.NUMERIC_FIELDS[name]
                    num_val = self._to_float(value)
                    meta[num_key] = num_val
                    nutr[num_key] = num_val
                    continue

                if name == "bisgNwBaseUnit":  # g / ml
                    nutrition_unit = value.strip().lower()
                    meta["nutrition_unit"] = nutrition_unit
                    continue

            # ---------------- nutrition block ----------------------
            nutr_lines: List[str] = []
            if any(k in nutr for k in ("kj_per_100", "kcal_per_100", "fat_g_per_100")):
                nutr_lines.append(f"Nährwerte (je 100 {nutrition_unit}):")
                kj = nutr.get("kj_per_100")
                kcal = nutr.get("kcal_per_100")
                if kj is not None or kcal is not None:
                    kj_txt = f"{int(kj)} kJ" if kj is not None else ""
                    kcal_txt = f"{int(kcal)} kcal" if kcal is not None else ""
                    nutr_lines.append(f"  Energie: {kj_txt}{' / ' if kj_txt and kcal_txt else ''}{kcal_txt}".rstrip())

                def add(label: str, key: str):
                    val = nutr.get(key)
                    if val is not None:
                        nutr_lines.append(f"  {label}: {val:g} g")

                add("Fett", "fat_g_per_100")
                add("  davon gesättigte Fettsäuren", "sat_fat_g_per_100")
                add("Kohlenhydrate", "carbs_g_per_100")
                add("  davon Zucker", "sugar_g_per_100")
                add("Eiweiß", "protein_g_per_100")
                if nutr.get("salt_g_per_100") is not None:
                    nutr_lines.append(f"  Salz: {nutr['salt_g_per_100']:g} g")

            # ---------------- price & shipping display --------------
            if self._price_missing(price_raw):
                price_line = "Preis: Nicht angegeben, bitte schaue über den Link nach"
            else:
                price_line = f"Preis: {price_raw}"

            if self._price_missing(ship_price_raw):
                ship_line = "Versand: Nicht angegeben, bitte schaue über den Link nach"
            else:
                ship_line = f"Versand: {ship_price_raw} ({ship_country})" if ship_country else f"Versand: {ship_price_raw}"

            # ---------------- assemble page_content ----------------
            parts = [
                f"Name: {title}",
                f"Marke: {brand}" if brand else "",
                f"Beschreibung: {description}" if description else "",
                *attr_lines,
                *text_lines,
                *nutr_lines,
                f"Link: {link}",
                price_line,
                ship_line,
            ]
            page_content = "\n".join(p for p in parts if p)

            yield Document(page_content=page_content, metadata=meta)


# ====================================================================== #
#   M A I N                                                              #
# ====================================================================== #
def main() -> None:
    # Load environment variables
    load_dotenv()
    krb_url = os.getenv("KRB_URL")
    if not krb_url:
        raise EnvironmentError("KRB_URL not set in environment or .env")

    # Initialize XML loader
    loader = KRBXMLLoader(krb_url)

    # Append output directly to log.txt
    with open("log.txt", "a", encoding="utf-8") as log_file:
        for idx, doc in enumerate(loader.lazy_load(), 1):
            divider = "=" * 90
            # Write document header
            log_file.write(f"{divider}\nD O C U M E N T  {idx}\n{divider}\n")
            # Write page content
            log_file.write(textwrap.indent(doc.page_content, "  "))
            log_file.write("\n\n  ── metadata ─────────────────────────────────────────\n")
            # Write metadata entries
            for k, v in doc.metadata.items():
                log_file.write(f"  {k}: {v}\n")
            log_file.write("\n")

if __name__ == "__main__":
    main()