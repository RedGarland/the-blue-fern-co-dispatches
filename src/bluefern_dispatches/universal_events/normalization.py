from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit


CORPORATE_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
}

STREET_SUFFIXES = {
    "avenue": "ave",
    "boulevard": "blvd",
    "circle": "cir",
    "court": "ct",
    "drive": "dr",
    "highway": "hwy",
    "lane": "ln",
    "parkway": "pkwy",
    "place": "pl",
    "road": "rd",
    "street": "st",
    "suite": "ste",
    "terrace": "ter",
}


def normalize_spaces(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split())


def normalize_name(value: object) -> str:
    text = normalize_spaces(value).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    words = [word for word in text.split() if word]
    while words and words[-1] in CORPORATE_SUFFIXES:
        words.pop()
    return " ".join(words)


def normalize_address(value: object) -> str:
    text = normalize_spaces(value).casefold()
    text = re.sub(r"[^\w\s#-]", " ", text)
    words = []
    for word in text.split():
        words.append(STREET_SUFFIXES.get(word, word))
    return " ".join(words)


def normalize_country_code(value: object) -> str:
    text = normalize_spaces(value).upper()
    if text in {"UNITED STATES", "UNITED STATES OF AMERICA", "USA"}:
        return "US"
    return text


def normalize_identifier(scheme: object, value: object) -> str:
    scheme_text = normalize_spaces(scheme).casefold()
    value_text = normalize_spaces(value)
    if scheme_text == "domain":
        raw = value_text
        if "://" not in raw:
            raw = f"https://{raw}"
        try:
            return urlsplit(raw).netloc.casefold().removeprefix("www.")
        except ValueError:
            return value_text.casefold().removeprefix("www.")
    if scheme_text in {"cms_ccn", "npi", "nces", "fips_state", "fips_county"}:
        return re.sub(r"[^0-9]", "", value_text)
    if scheme_text == "ein":
        return re.sub(r"[^0-9]", "", value_text)
    if scheme_text == "postal_code":
        return value_text.upper().split("-")[0]
    return value_text.casefold()


def normalized_address_parts(
    *,
    address_line_1: str | None = None,
    address_line_2: str | None = None,
    locality: str | None = None,
    region: str | None = None,
    postal_code: str | None = None,
    country_code: str | None = None,
) -> str:
    return "|".join(
        [
            normalize_address(address_line_1),
            normalize_address(address_line_2),
            normalize_name(locality),
            normalize_name(region),
            normalize_identifier("postal_code", postal_code),
            normalize_country_code(country_code),
        ]
    )
