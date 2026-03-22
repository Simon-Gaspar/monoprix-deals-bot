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
from datetime import datetime, timedelta, timezone
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


# Mapping mots-clés → catégorie pour les produits classés "Promotions" par l'API
CATEGORY_KEYWORDS = {
    "Produits Laitiers": [
        "yaourt", "lait ", "crème", "beurre", "fromage", "mozzarella", "emmental",
        "comté", "camembert", "brie", "gruyère", "skyr", "dessert", "flan",
        "mousse", "petit suisse", "perle de lait", "danette", "activia", "danone",
        "yoplait", "gervais", "kiri", "danonino", "philadelphia", "pérac",
        "apérivrais", "margarine", "hubert", "sorbet", "glace",
    ],
    "Bière et Alcools": [
        "bière", "blonde", " ipa ", "pale ale", "stout", "brune", "lager",
        "cidre", "vin ", "champagne", "whisky", "rhum", "vodka", "gin ",
        "corona", "heineken", "guinness", "1664", "brewdog", "galibier",
        "pelican", "pélican", "crémant", "luberon", "ventoux", "cabernet",
        "merlot", "rosé", "igp ", "aop ",
    ],
    "Épicerie Sucrée": [
        "biscuit", "chocolat", "gâteau", "cookie", "bonbon", "confiture",
        "miel", "pâte à tartiner", "céréale", "granola", "nutella", "ferrero",
        "kinder", "lindt", "lutti", "gerblé", "brioche", "pain de mie",
        "muesli", "bjorg", "sablé", "œuf", "oeuf", "abtey",
    ],
    "Épicerie Salée": [
        "pâtes", "riz ", "sauce", "huile", "vinaigre", "conserve", "soupe",
        "bouillon", "condiment", "moutarde", "ketchup", "mayonnaise", "olive",
        "cornichon", "chips", "apéritif", "crackers", "benenuts", "bénénuts",
        "maggi", "tipiak", "old el paso", "cacahuète", "noix de cajou",
        "amande", "noix", "raisin", "seeberger",
    ],
    "Boissons": [
        "jus", "eau ", "soda", "thé ", "café", "boisson", "sirop",
        "limonade", "coca", "orangina", "oasis", "evian", "vittel",
        "perrier", "tropicana", "innocent", "pressade", "teisseire",
        "salvetat", "lorina", "starbucks", "lipton", "clipper", "oatly",
        "avoine", "danao", "infusion", "heroic", "prime ", "hydratation",
    ],
    "Viandes et Charcuterie": [
        "jambon", "saucisson", "poulet", "bœuf", "porc", "veau", "agneau",
        "steak", "côte", "escalope", "rôti", "lardons", "bacon", "pâté",
        "rillettes", "terrine", "saucisse", "merguez", "charal", "socopa",
        "fleury michon", "herta", "cochonou", "citterio", "maître coq",
        "daunat", "bresaola", "lard", "dinde", "ailes", "saint agaûne",
        "henri raffin", "bûchette", "villani",
    ],
    "Poissons et Fruits de mer": [
        "saumon", "thon", "crevette", "poisson", "cabillaud", "truite",
        "sardine", "hareng", "surimi", "labeyrie",
    ],
    "Hygiène et Beauté": [
        "shampooing", "gel douche", "dentifrice", "déodorant", "savon",
        "lotion", "elsève", "dessange", "sanex", "l'oréal", "pampers",
    ],
    "Entretien": [
        "lessive", "liquide vaisselle", "nettoyant", "éponge", "javel", "lotus",
    ],
    "Fruits et Légumes": [
        "salade", "tomate", "pomme", "banane", "orange", "carotte",
        "courgette", "florette", "bonduelle", "compote", "pom'potes",
    ],
}


# Normalise les catégories de l'API vers nos catégories unifiées
CATEGORY_NORMALIZE = {
    "Produits Laitiers, Œufs et Fromages": "Produits Laitiers",
    "Boissons et Lait": "Boissons",
    "Bière, Cave et Spiritueux": "Bière et Alcools",
    "Charcuterie et Traiteur": "Viandes et Charcuterie",
    "Boucherie et Volaille": "Viandes et Charcuterie",
    "Epicerie Sucrée": "Épicerie Sucrée",
    "Epicerie Salée": "Épicerie Salée",
    "Pain et Viennoiserie": "Épicerie Sucrée",
    "Entretien & Nettoyage": "Entretien",
    "Pâques": "Épicerie Sucrée",
    "Nos sélections": "",  # will be guessed from name
    "Nos recettes": "",
}


def guess_category(name: str) -> str:
    """Déduit la catégorie d'un produit à partir de son nom."""
    name_lower = name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return category
    return "Autre"


# Sous-catégories de recherche pour les prix de référence.
# Chaque terme = une recherche API → une médiane distincte.
# Les mots-clés servent à associer un produit promo au bon terme.
# IMPORTANT : l'ordre compte ! Les sous-catégories plus spécifiques
# doivent être listées AVANT les plus génériques pour éviter les faux matchs.
SUBCATEGORY_TERMS = [
    # Fruits et Légumes (avant "compote" qui matche "pommes")
    {"term": "salade", "keywords": ["salade", "florette", "bonduelle", "tortis"]},
    # Épicerie Sucrée (avant "lait" pour que "chocolat au lait" → chocolat)
    {"term": "chocolat", "keywords": ["chocolat", "lindt", "ferrero", "kinder", "nutella"]},
    {"term": "biscuit", "keywords": ["biscuit", "cookie", "sablé", "gerblé", "granola", "lu "]},
    {"term": "confiture", "keywords": ["confiture", "miel", "pâte à tartiner", "bonne maman"]},
    {"term": "céréales", "keywords": ["céréale", "muesli", "bjorg"]},
    {"term": "brioche", "keywords": ["brioche", "pain de mie", "harrys", "viennoiserie"]},
    {"term": "bonbon", "keywords": ["bonbon", "lutti", "œuf", "oeuf", "abtey"]},
    # Produits Laitiers
    {"term": "yaourt", "keywords": ["yaourt", "skyr", "perle de lait", "activia", "danone", "yoplait", "danonino", "petit suisse", "gervais", "siggi"]},
    {"term": "fromage", "keywords": ["fromage", "mozzarella", "emmental", "comté", "camembert", "brie", "gruyère", "gouda", "kiri", "pérac", "apérivrais", "philadelphia", "etorki", "caprice"]},
    {"term": "beurre", "keywords": ["beurre", "margarine", "hubert", "elle & vire"]},
    {"term": "crème dessert", "keywords": ["danette", "flan", "mousse au", "crème dessert", "laitière"]},
    {"term": "compote", "keywords": ["compote", "pom'potes", "pommes", "fruits", "charles"]},
    {"term": "sorbet", "keywords": ["sorbet", "glace"]},
    {"term": "lait", "keywords": ["lait ", " lait", "lactel", "elben"]},
    # Bière et Alcools
    {"term": "bière", "keywords": ["bière", "blonde", " ipa ", "stout", "brune", "corona", "heineken", "guinness", "1664", "brewdog", "galibier", "pelican", "pélican"]},
    {"term": "vin rouge", "keywords": ["vin ", "cabernet", "merlot", "ventoux", "luberon", "igp ", "aop ", "crémant", "rosé"]},
    # Boissons
    {"term": "jus de fruits", "keywords": ["jus", "tropicana", "pressade", "innocent", "danao"]},
    {"term": "eau minérale", "keywords": ["eau ", "vittel", "salvetat", "evian", "perrier"]},
    {"term": "soda", "keywords": ["soda", "limonade", "lorina", "coca"]},
    {"term": "thé", "keywords": ["thé ", "infusion", "lipton", "clipper"]},
    {"term": "boisson énergétique", "keywords": ["boisson", "oatly", "avoine", "heroic", "prime ", "hydratation", "starbucks", "café"]},
    {"term": "sirop", "keywords": ["sirop", "teisseire"]},
    # Épicerie Salée
    {"term": "pâtes", "keywords": ["pâtes"]},
    {"term": "riz", "keywords": ["riz "]},
    {"term": "sauce", "keywords": ["sauce", "ketchup", "mayonnaise", "moutarde", "maggi", "old el paso"]},
    {"term": "chips", "keywords": ["chips", "cacahuète", "bénénuts", "benenuts", "apéritif"]},
    {"term": "huile", "keywords": ["huile", "olive", "vinaigre"]},
    {"term": "noix", "keywords": ["noix", "amande", "cajou", "seeberger", "raisin"]},
    {"term": "conserve", "keywords": ["conserve", "soupe", "bouillon", "tipiak"]},
    # Viandes et Charcuterie
    {"term": "jambon", "keywords": ["jambon", "fleury michon", "herta"]},
    {"term": "poulet", "keywords": ["poulet", "dinde", "ailes", "maître coq", "loué", "cordon bleu"]},
    {"term": "saucisson", "keywords": ["saucisson", "saint agaûne", "henri raffin", "bûchette", "cochonou"]},
    {"term": "steak", "keywords": ["steak", "bœuf", "charal", "socopa", "lardons", "lard", "viande"]},
    {"term": "bresaola", "keywords": ["bresaola", "villani", "citterio", "daunat"]},
    # Poissons
    {"term": "saumon", "keywords": ["saumon", "labeyrie"]},
    {"term": "thon", "keywords": ["thon", "crevette", "poisson", "surimi"]},
    # Hygiène
    {"term": "shampooing", "keywords": ["shampooing", "elsève", "dessange", "l'oréal"]},
    {"term": "gel douche", "keywords": ["gel douche", "savon", "sanex", "déodorant", "dentifrice"]},
    {"term": "couches", "keywords": ["pampers", "couche"]},
    # Entretien
    {"term": "lessive", "keywords": ["lessive", "liquide vaisselle", "nettoyant", "javel", "éponge"]},
    {"term": "mouchoirs", "keywords": ["mouchoir", "lotus"]},
]


def match_subcategory(product_name):
    """Trouve le terme de sous-catégorie le plus pertinent pour un produit."""
    name_lower = product_name.lower()
    for sub in SUBCATEGORY_TERMS:
        for kw in sub["keywords"]:
            if kw in name_lower:
                return sub["term"]
    return None


def fetch_reference_prices():
    """
    Récupère les prix unitaires médians par sous-catégorie+unité.
    Retourne: {"yaourt|/kg": {"median": 5.60, "count": 295, "label": "yaourt"}, ...}
    """
    from statistics import median

    ref = {}
    seen_terms = set()
    terms_to_fetch = [sub["term"] for sub in SUBCATEGORY_TERMS]

    for term in terms_to_fetch:
        if term in seen_terms:
            continue
        seen_terms.add(term)

        try:
            r = requests.get(
                COURSES_SEARCH_URL,
                params={
                    "maxPageSize": "300",
                    "maxProductsToDecorate": "300",
                    "q": term,
                    "tag": "web",
                },
                headers=HEADERS,
                timeout=15,
            )
            if not r.ok:
                continue
        except Exception:
            continue

        data = r.json()
        prices_by_unit = {}
        for group in data.get("productGroups", []):
            products = group.get("decoratedProducts") or group.get("products", [])
            for p in products:
                up = p.get("unitPrice", {})
                amount = up.get("price", {}).get("amount", "")
                unit_raw = up.get("unit", "")
                if not amount or not unit_raw:
                    continue
                unit_label = unit_raw.replace("fop.price.per.", "/")
                prices_by_unit.setdefault(unit_label, []).append(float(amount))

        for unit_label, prices in prices_by_unit.items():
            if len(prices) >= 5:
                key = f"{term}|{unit_label}"
                ref[key] = {
                    "median": round(median(prices), 2),
                    "count": len(prices),
                    "label": term,
                }
                print(f"  [REF] {key}: médiane={ref[key]['median']} €  (n={len(prices)})")

    return ref


def discount_factor(desc: str) -> float:
    """
    Calcule le facteur multiplicatif moyen pour une promo.
    Ex: -50% sur le 2ème → on paie 1.5 pour 2 → facteur 0.75
        2+1 offert → on paie 2 pour 3 → facteur 0.667
        -30% remise immédiate → facteur 0.70
    """
    d = desc.lower()
    # "X+Y offert" ou "X +Y offert"
    m = re.search(r"(\d+)\s*\+\s*(\d+)\s*offert", d)
    if m:
        pay = int(m.group(1))
        free = int(m.group(2))
        return pay / (pay + free)
    # "-XX% sur le 2ème/Nème produit"
    m = re.search(r"-(\d+)%\s*sur le (\d+)", d)
    if m:
        pct = int(m.group(1))
        nth = int(m.group(2))
        # On paie (nth-1) + (1 - pct/100) pour nth produits
        return ((nth - 1) + (1 - pct / 100)) / nth
    # "-XX% remise immédiate"
    m = re.search(r"-(\d+)%", d)
    if m:
        return 1 - int(m.group(1)) / 100
    return 1.0


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
            raw_category = category_path[0] if category_path else "Autre"

            # Normaliser la catégorie API, puis deviner si nécessaire
            category = CATEGORY_NORMALIZE.get(raw_category, raw_category)
            if not category or category == "Promotions":
                category = guess_category(product.get("name", ""))

            image_path = (product.get("imagePaths") or [""])[0]
            image = f"{image_path}/200x200.webp" if image_path else ""

            unit_price_data = product.get("unitPrice", {})
            unit_amount = unit_price_data.get("price", {}).get("amount", "")
            unit_raw = unit_price_data.get("unit", "")
            unit_label = unit_raw.replace("fop.price.per.", "/") if unit_raw else ""

            # Prix unitaire après réduction
            discounted_unit = ""
            if unit_amount:
                factor = discount_factor(short_desc)
                discounted = float(unit_amount) * factor
                discounted_unit = f"{discounted:.2f} €{unit_label}"

            subcategory = match_subcategory(product.get("name", ""))
            product_id = product.get("retailerProductId", "")

            deals.append({
                "id": product_id,
                "name": product.get("name", ""),
                "price": price,
                "discount": short_desc,
                "category": category,
                "subcategory": subcategory or "",
                "image": image,
                "unitPrice": f"{unit_amount} €{unit_label}" if unit_amount else "",
                "discountedUnitPrice": discounted_unit,
            })

    return deals


HISTORY_MAX_DAYS = 90


def update_history(deals):
    """Met à jour l'historique des prix (rolling 90 jours)."""
    os.makedirs(DOCS_DIR, exist_ok=True)
    history_path = os.path.join(DOCS_DIR, "history.json")

    # Charger l'historique existant
    history = {}
    if os.path.exists(history_path):
        with open(history_path, "r", encoding="utf-8") as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = {}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_MAX_DAYS)).strftime("%Y-%m-%d")

    for deal in deals:
        pid = deal.get("id", "")
        if not pid:
            continue

        if pid not in history:
            history[pid] = {"name": deal["name"], "prices": {}}

        # Mettre à jour le nom (peut changer légèrement)
        history[pid]["name"] = deal["name"]

        # Ajouter le prix du jour
        entry = {"price": deal["price"]}
        if deal.get("unitPrice"):
            entry["unitPrice"] = deal["unitPrice"].split(" ")[0]  # "5.18" from "5.18 €/kg"
        if deal.get("discount"):
            entry["discount"] = deal["discount"]
        history[pid]["prices"][today] = entry

        # Prune les entrées trop anciennes
        history[pid]["prices"] = {
            d: v for d, v in history[pid]["prices"].items() if d >= cutoff
        }

    # Prune les produits sans données récentes
    history = {
        pid: data for pid, data in history.items()
        if any(d >= cutoff for d in data["prices"])
    }

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[OK] Historique mis à jour: {len(history)} produits dans {history_path}")


def export_json(deals, reference_prices=None):
    """Exporte les promos en JSON dans docs/data.json."""
    os.makedirs(DOCS_DIR, exist_ok=True)
    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "count": len(deals),
        "deals": deals,
        "referencePrices": reference_prices or {},
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

    print("[INFO] Récupération des prix de référence par rayon...")
    ref_prices = fetch_reference_prices()
    print(f"[INFO] {len(ref_prices)} références de prix récupérées.")

    # Historique des prix + export JSON
    if deals:
        update_history(deals)
        export_json(deals, ref_prices)

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
