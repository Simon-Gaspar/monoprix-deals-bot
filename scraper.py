#!/usr/bin/env python3
"""
Bot deals Monoprix → Discord + front statique
Récupère les promos Monoprix via l'API courses.monoprix.fr,
notifie sur Discord et génère un JSON pour le front.
"""

import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
import requests

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")

COURSES_SEARCH_URL = "https://courses.monoprix.fr/api/webproductpagews/v6/product-pages/search"
MONOPRIX_URL = "https://courses.monoprix.fr"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_all_promos() -> list[dict]:
    """
    Récupère toutes les promos via l'API courses.monoprix.fr.
    Retourne une liste de dicts avec name, price, discount, category.
    """
    params = {
        "maxPageSize": "300",
        "maxProductsToDecorate": "300",
        "q": "promo",
        "tag": "web",
    }
    url = COURSES_SEARCH_URL + "?" + urllib.parse.urlencode(params)

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            print(f"[WARN] courses API {r.status_code}")
            return []
    except Exception as e:
        print(f"[WARN] courses API erreur: {e}")
        return []

    data = r.json()
    deals = []

    for group in data.get("productGroups", []):
        products = group.get("decoratedProducts") or group.get("products", [])
        for product in products:
            promos = product.get("promotions", [])
            if not promos:
                continue
            desc = promos[0].get("description", "")
            short_desc = re.sub(r" - Cliquez.*", "", desc)
            price = product.get("price", {}).get("amount", "")
            category_path = product.get("categoryPath", [])
            category = category_path[0] if category_path else "Autre"

            deals.append({
                "name": product.get("name", ""),
                "price": price,
                "discount": short_desc,
                "category": category,
            })

    return deals


def export_json(deals: list[dict]) -> None:
    """Exporte les promos en JSON dans docs/data.json."""
    os.makedirs(DOCS_DIR, exist_ok=True)
    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "count": len(deals),
        "deals": deals,
    }
    path = os.path.join(DOCS_DIR, "data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[OK] {len(deals)} promos exportées dans {path}")


# --- Discord ---

BEST_PROMO_PATTERNS = ["2 +1 offert", "2+1 offert", "-68%", "-60%", "-50%"]
MAX_ITEMS_PER_PROMO = 5


def is_best_promo(desc: str) -> bool:
    desc_lower = desc.lower()
    return any(p.lower() in desc_lower for p in BEST_PROMO_PATTERNS)


def build_discord_message(deals: list[dict]) -> str:
    today = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    best = [d for d in deals if is_best_promo(d["discount"])]
    lines = [f"**Meilleures promos Monoprix** — {today}\n"]

    if best:
        by_promo = {}
        for deal in best:
            by_promo.setdefault(deal["discount"], []).append(deal)

        def promo_sort_key(item):
            desc = item[0].lower()
            if "2 +1" in desc or "2+1" in desc:
                return 0
            if "-68%" in desc:
                return 1
            if "-60%" in desc:
                return 2
            if "-50%" in desc:
                return 3
            return 4

        for promo_type, items in sorted(by_promo.items(), key=promo_sort_key):
            lines.append(f"\n**{promo_type}** ({len(items)} produits)")
            for item in items[:MAX_ITEMS_PER_PROMO]:
                price_str = f" — {item['price']} €" if item.get("price") else ""
                lines.append(f"• {item['name']}{price_str}")
            if len(items) > MAX_ITEMS_PER_PROMO:
                lines.append(f"  *… et {len(items) - MAX_ITEMS_PER_PROMO} autres*")
    else:
        lines.append("Aucune grosse promo trouvée aujourd'hui.")

    lines.append(f"\n[Voir toutes les promos]({MONOPRIX_URL})")
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
    notify_if_empty = config.get("notify_if_empty", False)

    print("[INFO] Recherche des promos via courses.monoprix.fr...")
    deals = fetch_all_promos()
    print(f"[INFO] {len(deals)} promo(s) trouvée(s).")

    # Export JSON pour le front
    if deals:
        export_json(deals)

    # Discord
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if webhook_url:
        best = [d for d in deals if is_best_promo(d["discount"])]
        if best or notify_if_empty:
            message = build_discord_message(deals)
            send_discord(webhook_url, message)
        else:
            print("[INFO] Aucune grosse promo. Pas de notification Discord.")
    else:
        print("[INFO] DISCORD_WEBHOOK_URL non définie, skip Discord.")


if __name__ == "__main__":
    main()
