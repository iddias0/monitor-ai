#!/usr/bin/env python3
"""
Ad Tracker — coleta diária de dados da Biblioteca de Anúncios (Meta Ad Library).

O que faz:
  1. Lê a lista de ofertas rastreadas em docs/data/offers.json
  2. Para cada oferta, abre a URL da Biblioteca de Anúncios com um navegador
     headless e extrai os anúncios ativos (ID, texto, tipo de mídia, data de início)
  3. Compara com o snapshot do dia anterior para calcular o que é novo,
     o que foi desativado e o que continua rodando (e há quanto tempo)
  4. Salva tudo em JSON dentro de docs/ (para o dashboard estático ler)

Este script é executado automaticamente todo dia pelo GitHub Actions
(.github/workflows/daily-scrape.yml), mas também pode ser rodado manualmente:

    python scraper/scrape.py

IMPORTANTE — leia antes de rodar pela primeira vez:
  A Meta muda o HTML da Biblioteca de Anúncios com alguma frequência e não
  oferece uma API pública para anúncios comerciais fora da UE/Reino Unido
  (por isso este script lê a página pública, não uma API oficial). Isso
  significa que os seletores/regex abaixo podem parar de funcionar em algum
  momento. Se isso acontecer, rode com DEBUG=1 (salva o HTML bruto de cada
  card em scraper/debug/) e ajuste as expressões regulares em `parse_card`.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
HISTORY_DIR = DATA_DIR / "history"
DIFFS_DIR = DATA_DIR / "diffs"
FIRST_SEEN_DIR = DATA_DIR / "first_seen"
OFFERS_FILE = DATA_DIR / "offers.json"
DEBUG = os.environ.get("DEBUG") == "1"
DEBUG_DIR = Path(__file__).resolve().parent / "debug"

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# Textos que indicam "esta é uma caixa de anúncio individual" — usamos texto
# em vez de nomes de classe CSS porque a Meta ofusca/troca os nomes de classe
# com frequência, mas o texto da interface muda bem menos.
LIBRARY_ID_MARKERS = ["Library ID:", "ID da biblioteca:"]

STARTED_PATTERNS = [
    r"Started running on ([^\n·]+)",
    r"Ativo desde ([^\n·]+)",
    r"Veiculado desde ([^\n·]+)",
    r"Começou a ser exibido em ([^\n·]+)",
]

LIBRARY_ID_PATTERNS = [
    r"Library ID:\s*([0-9]+)",
    r"ID da biblioteca:\s*([0-9]+)",
]


def load_offers():
    if not OFFERS_FILE.exists():
        print(f"Arquivo de ofertas não encontrado: {OFFERS_FILE}")
        sys.exit(1)
    with open(OFFERS_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    return [o for o in config.get("offers", []) if o.get("active", True)]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "oferta"


def autoscroll(page, max_scrolls=40, pause_ms=900):
    """Rola a página até o número de anúncios carregados parar de crescer
    (a Biblioteca de Anúncios carrega resultados via scroll infinito)."""
    last_count = -1
    stable_rounds = 0
    for _ in range(max_scrolls):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(pause_ms)
        count = page.evaluate(
            """() => document.body.innerText.split('Library ID:').length
                     + document.body.innerText.split('ID da biblioteca:').length"""
        )
        if count == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_count = count
        if stable_rounds >= 3:
            break


def dismiss_cookie_banner(page):
    """Tenta fechar o banner de cookies, se aparecer. Não é crítico se falhar."""
    candidates = [
        "Allow all cookies",
        "Permitir todos os cookies",
        "Aceitar todos",
        "Accept all",
        "Only allow essential cookies",
    ]
    for text in candidates:
        try:
            btn = page.get_by_role("button", name=text, exact=False)
            if btn.count() > 0:
                btn.first.click(timeout=2000)
                page.wait_for_timeout(500)
                return
        except Exception:
            pass


def extract_card_texts(page):
    """Retorna uma lista de blocos de texto, um por card de anúncio."""
    js = """
    () => {
        const markers = ["Library ID:", "ID da biblioteca:"];
        const all = Array.from(document.querySelectorAll('div'));
        const leafHits = all.filter(el => {
            if (el.children.length > 2) return false;
            const t = el.innerText || "";
            return markers.some(m => t.trim().startsWith(m));
        });
        const seen = new Set();
        const cards = [];
        for (const node of leafHits) {
            let card = node;
            // sobe alguns níveis até achar um container com tamanho razoável
            for (let i = 0; i < 8 && card.parentElement; i++) {
                card = card.parentElement;
                const len = (card.innerText || "").length;
                if (len > 120 && len < 4000) break;
            }
            const text = card.innerText || "";
            if (!seen.has(text)) {
                seen.add(text);
                cards.push(text);
            }
        }
        return cards;
    }
    """
    return page.evaluate(js)


def parse_card(text: str):
    lib_id = None
    for pat in LIBRARY_ID_PATTERNS:
        m = re.search(pat, text)
        if m:
            lib_id = m.group(1)
            break
    if not lib_id:
        return None

    started_raw = None
    for pat in STARTED_PATTERNS:
        m = re.search(pat, text)
        if m:
            started_raw = m.group(1).strip()
            break

    media_type = "desconhecido"
    lowered = text.lower()
    if "vídeo" in lowered or "video" in lowered:
        media_type = "video"
    elif "carrossel" in lowered or "carousel" in lowered:
        media_type = "carrossel"
    elif "imagem" in lowered or "image" in lowered:
        media_type = "imagem"

    platforms = []
    for plat in ["Facebook", "Instagram", "Messenger", "Audience Network", "Threads"]:
        if plat in text:
            platforms.append(plat)

    # primeira linha "útil" de texto do anúncio (heurística — melhor esforço)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    snippet = ""
    for l in lines:
        if len(l) > 25 and "Library ID" not in l and "ID da biblioteca" not in l:
            snippet = l[:200]
            break

    return {
        "library_id": lib_id,
        "started_raw": started_raw,
        "media_type": media_type,
        "platforms": platforms,
        "snippet": snippet,
    }


def scrape_offer(playwright, offer):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        locale="pt-BR",
        viewport={"width": 1440, "height": 1000},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = context.new_page()
    ads = []
    try:
        page.goto(offer["url"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        dismiss_cookie_banner(page)
        page.wait_for_timeout(1000)
        autoscroll(page)

        raw_cards = extract_card_texts(page)

        if DEBUG:
            DEBUG_DIR.mkdir(exist_ok=True)
            debug_file = DEBUG_DIR / f"{slugify(offer['name'])}_{TODAY}.txt"
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write("\n\n---CARD---\n\n".join(raw_cards))

        for raw in raw_cards:
            parsed = parse_card(raw)
            if parsed:
                ads.append(parsed)

    finally:
        context.close()
        browser.close()

    # dedup por library_id
    dedup = {}
    for ad in ads:
        dedup[ad["library_id"]] = ad
    return list(dedup.values())


def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def latest_history_file(slug: str, before_date: str):
    offer_dir = HISTORY_DIR / slug
    if not offer_dir.exists():
        return None
    dates = sorted(
        p.stem for p in offer_dir.glob("*.json") if p.stem < before_date
    )
    if not dates:
        return None
    return offer_dir / f"{dates[-1]}.json"


def process_offer(offer, today_ads):
    slug = slugify(offer["name"])
    today_ids = {a["library_id"] for a in today_ads}

    # first_seen: mapa library_id -> primeira data em que apareceu
    first_seen_path = FIRST_SEEN_DIR / f"{slug}.json"
    first_seen = load_json(first_seen_path, {})
    for aid in today_ids:
        if aid not in first_seen:
            first_seen[aid] = TODAY
    save_json(first_seen_path, first_seen)

    # snapshot de hoje
    save_json(HISTORY_DIR / slug / f"{TODAY}.json", today_ads)

    # snapshot anterior mais recente
    prev_file = latest_history_file(slug, TODAY)
    prev_ads = load_json(prev_file, []) if prev_file else []
    prev_ids = {a["library_id"] for a in prev_ads}

    new_ids = today_ids - prev_ids
    removed_ids = prev_ids - today_ids
    surviving_ids = today_ids & prev_ids

    def days_running(aid):
        started = first_seen.get(aid, TODAY)
        d0 = datetime.strptime(started, "%Y-%m-%d")
        d1 = datetime.strptime(TODAY, "%Y-%m-%d")
        return (d1 - d0).days

    diff_entry = {
        "date": TODAY,
        "total_active": len(today_ads),
        "new_count": len(new_ids),
        "removed_count": len(removed_ids),
        "surviving_count": len(surviving_ids),
        "new_ads": [a for a in today_ads if a["library_id"] in new_ids],
        "removed_ads": [a for a in prev_ads if a["library_id"] in removed_ids],
        "top_survivors": sorted(
            [
                {**a, "days_running": days_running(a["library_id"])}
                for a in today_ads
            ],
            key=lambda a: a["days_running"],
            reverse=True,
        )[:10],
    }

    diffs_path = DIFFS_DIR / f"{slug}.json"
    diffs = load_json(diffs_path, [])
    diffs = [d for d in diffs if d["date"] != TODAY]  # evita duplicar se rodar 2x no mesmo dia
    diffs.append(diff_entry)
    diffs = diffs[-120:]  # mantém ~4 meses de histórico
    save_json(diffs_path, diffs)

    return diff_entry


def main():
    offers = load_offers()
    if not offers:
        print("Nenhuma oferta ativa em offers.json. Nada a fazer.")
        return

    summary = []
    with sync_playwright() as p:
        for offer in offers:
            print(f"→ Coletando: {offer['name']}")
            try:
                ads = scrape_offer(p, offer)
                diff = process_offer(offer, ads)
                print(
                    f"  ok: {diff['total_active']} ativos | "
                    f"+{diff['new_count']} novos | -{diff['removed_count']} removidos"
                )
                summary.append({"offer": offer["name"], "slug": slugify(offer["name"]), "ok": True, **diff})
            except Exception as e:
                print(f"  ERRO em '{offer['name']}': {e}")
                summary.append({"offer": offer["name"], "ok": False, "error": str(e)})
            time.sleep(3)  # respiro entre ofertas

    save_json(DATA_DIR / "last_run.json", {"date": TODAY, "results": summary})
    print("Concluído.")


if __name__ == "__main__":
    main()
