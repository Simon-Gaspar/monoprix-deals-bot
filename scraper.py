#!/usr/bin/env python3
"""
Bot deals Monoprix Annecy → Discord
Scrape le catalogue Monoprix et notifie sur Discord si des produits surveillés sont en promo.
"""

import json
import os
import unicodedata
import urllib.parse
from datetime import date
from playwright.sync_api import sync_playwright
import requests

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

MONOPRIX_URL = "https://catalogue.monoprix.fr/"
BONIAL_URL = "https://www.bonial.fr/Magasins/Annecy/Monoprix/v-r17"
COURSES_SEARCH_URL = "https://courses.monoprix.fr/api/webproductpagews/v6/product-pages/search"


def normalize(text: str) -> str:
    """Supprime les accents et met en minuscules pour la comparaison."""
    return unicodedata.normalize("NFD", text.lower()).encode("ascii", "ignore").decode()


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Source 1 : courses.monoprix.fr — API JSON directe (pas de Playwright)
# ---------------------------------------------------------------------------

def search_courses_api(keywords: list[str]) -> list[dict]:
    """
    Cherche chaque mot-clé via l'API courses.monoprix.fr et retourne uniquement
    les produits qui ont une promotion active.
    """
    matched = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    for kw in keywords:
        params = {
            "includeAdditionalPageInfo": "true",
            "maxPageSize": "300",
            "maxProductsToDecorate": "50",
            "q": kw,
            "tag": "web",
        }
        try:
            url = COURSES_SEARCH_URL + "?" + urllib.parse.urlencode(params)
            r = requests.get(url, headers=headers, timeout=15)
            if not r.ok:
                print(f"[WARN] courses API {r.status_code} pour '{kw}'")
                continue
            data = r.json()
            for group in data.get("productGroups", []):
                products = group.get("decoratedProducts") or group.get("products", [])
                for product in products:
                    promos = product.get("promotions", [])
                    if promos:
                        promo_desc = promos[0].get("description", "")
                        price = product.get("price", {}).get("amount", "")
                        matched.append({
                            "name": product.get("name", kw),
                            "price": f"{price} €" if price else "",
                            "discount": promo_desc,
                        })
        except Exception as e:
            print(f"[WARN] courses API erreur pour '{kw}': {e}")
    return matched


# ---------------------------------------------------------------------------
# Source 2 : Playwright (catalogue.monoprix.fr, bonial.fr)
# ---------------------------------------------------------------------------

def scrape_deals(url: str) -> list[dict]:
    """
    Scrape la page avec Playwright et extrait les produits en promo.
    Retourne une liste de dicts : {"name": str, "price": str, "discount": str}
    """
    deals = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            # Fallback : extraction depuis le DOM
            deals = extract_from_dom(page)

        except Exception as e:
            print(f"[WARN] Erreur sur {url}: {e}")
        finally:
            browser.close()

    return deals


def extract_from_dom(page) -> list[dict]:
    """Extraction générique depuis le DOM rendu."""
    deals = []
    try:
        content = page.inner_text("body")
        deals.append({"name": "__RAW__", "price": "", "discount": "", "_raw": content})
    except Exception as e:
        print(f"[WARN] extract_from_dom: {e}")
    return deals


def filter_deals(deals: list[dict], keywords: list[str]) -> list[dict]:
    """Filtre les deals contenant au moins un mot-clé."""
    normalized_keywords = [normalize(k) for k in keywords]
    matched = []

    for deal in deals:
        if deal.get("name") == "__RAW__":
            raw = deal.get("_raw", "")
            norm_raw = normalize(raw)
            for kw, kw_norm in zip(keywords, normalized_keywords):
                if kw_norm in norm_raw:
                    idx = norm_raw.find(kw_norm)
                    excerpt = raw[max(0, idx - 30) : idx + 80].strip().replace("\n", " ")
                    matched.append({"name": kw, "price": "", "discount": "", "excerpt": excerpt})
        else:
            norm_name = normalize(deal.get("name", ""))
            for kw, kw_norm in zip(keywords, normalized_keywords):
                if kw_norm in norm_name:
                    matched.append(deal)
                    break

    return matched


def build_discord_message(matched: list[dict], keywords: list[str]) -> str:
    today = date.today().strftime("%d/%m/%Y")
    lines = [f"**Deals Monoprix Annecy** — {today}\n"]

    if matched:
        for deal in matched:
            if "excerpt" in deal:
                lines.append(f"**{deal['name'].capitalize()}** : ...{deal['excerpt']}...")
            else:
                parts = [f"**{deal['name']}**"]
                if deal.get("discount"):
                    parts.append(deal["discount"])
                if deal.get("price"):
                    parts.append(deal["price"])
                lines.append("  ".join(parts))
    else:
        lines.append("Aucun deal trouvé pour : " + ", ".join(keywords))

    lines.append(f"\n[Voir le catalogue]({MONOPRIX_URL})")
    return "\n".join(lines)


def send_discord(webhook_url: str, message: str) -> None:
    payload = {"content": message}
    response = requests.post(webhook_url, json=payload, timeout=10)
    if response.status_code not in (200, 204):
        print(f"[ERROR] Discord webhook failed: {response.status_code} {response.text}")
    else:
        print("[OK] Message envoyé sur Discord.")


def main():
    config = load_config()
    keywords = config.get("keywords", [])
    notify_if_empty = config.get("notify_if_empty", False)

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("[ERROR] Variable DISCORD_WEBHOOK_URL manquante.")
        return

    if not keywords:
        print("[WARN] Aucun mot-clé dans config.json.")
        return

    print(f"[INFO] Surveillance de : {keywords}")

    # Source 1 : API courses.monoprix.fr (rapide, fiable)
    print("[INFO] Recherche via courses.monoprix.fr API...")
    matched = search_courses_api(keywords)
    if matched:
        print(f"[INFO] {len(matched)} deal(s) trouvé(s) via courses.monoprix.fr.")
    else:
        print("[INFO] Pas de match sur courses.monoprix.fr, essai sources secondaires...")

        # Source 2 : catalogue.monoprix.fr + bonial (Playwright)
        for url, label in [
            (MONOPRIX_URL, "catalogue.monoprix.fr"),
            (BONIAL_URL, "Bonial"),
        ]:
            print(f"[INFO] Scraping {label}...")
            deals = scrape_deals(url)
            matched = filter_deals(deals, keywords)
            if matched:
                print(f"[INFO] Match trouvé sur {label}.")
                break
            print(f"[INFO] Pas de match sur {label}.")

    if matched or notify_if_empty:
        message = build_discord_message(matched, keywords)
        send_discord(webhook_url, message)
    else:
        print("[INFO] Aucun deal correspondant. Pas de notification.")


if __name__ == "__main__":
    main()
