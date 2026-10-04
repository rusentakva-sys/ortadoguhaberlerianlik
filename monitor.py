#!/usr/bin/env python3
"""Orta Doğu haber takibi -> Telegram.

Kullanım:
  python monitor.py alert    # yeni + anahtar kelimeli haberleri anında gönderir
  python monitor.py digest   # son 24 saatin özetini gönderir

Ortam değişkenleri: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import html
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import mktime

import feedparser
import requests

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SEEN_FILE = Path("seen.json")
MAX_ALERTS_PER_RUN = 15
DIGEST_PER_SOURCE = 8

feedparser.USER_AGENT = "Mozilla/5.0 (compatible; OrtaDoguTakip/1.0)"

# Bazı adresler zamanla değişebilir. Çalışmayanlar günlükte uyarı olarak görünür,
# betik diğer kaynaklarla devam eder. Kendi kaynaklarını buraya ekleyebilirsin.
FEEDS = {
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "BBC Middle East": "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
    "Guardian Middle East": "https://www.theguardian.com/world/middleeast/rss",
    "Times of Israel": "https://www.timesofisrael.com/feed/",
    "Middle East Eye": "https://www.middleeasteye.net/rss",
    "Al-Monitor": "https://www.al-monitor.com/rss",
    "Anadolu Ajansı": "https://www.aa.com.tr/tr/rss/default?cat=dunya",
    "BM Haberleri (Orta Doğu)": "https://news.un.org/feed/subscribe/en/news/region/middle-east/feed/rss.xml",
}

# Anlık uyarı için anahtar kelimeler (küçük harfle eşleşir). İstediğini ekle/çıkar.
KEYWORDS = [
    "breaking", "son dakika", "flash",
    "israel", "i̇srail", "israil", "gaza", "gazze", "hamas", "hezbollah", "hizbullah",
    "iran", "i̇ran", "tehran", "tahran", "syria", "suriye", "lebanon", "lübnan",
    "yemen", "houthi", "husi", "iraq", "irak", "red sea", "kızıldeniz",
    "ceasefire", "ateşkes", "airstrike", "hava saldırısı", "missile", "füze",
    "nuclear", "nükleer", "iaea", "uaea", "hormuz", "hürmüz",
    "security council", "güvenlik konseyi", "sanction", "yaptırım",
]


def entry_time(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    if not t:
        return None
    return datetime.fromtimestamp(mktime(t), tz=timezone.utc)


def fetch_all():
    items = []
    for source, url in FEEDS.items():
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                print(f"UYARI: {source} okunamadı", file=sys.stderr)
                continue
            for e in feed.entries:
                items.append({
                    "source": source,
                    "id": e.get("id") or e.get("link"),
                    "title": (e.get("title") or "").strip(),
                    "summary": (e.get("summary") or "")[:500],
                    "link": e.get("link"),
                    "time": entry_time(e),
                })
        except Exception as ex:  # tek kaynak hatası tümünü durdurmasın
            print(f"UYARI: {source}: {ex}", file=sys.stderr)
    return items


def send(text):
    # Telegram sınırı 4096 karakter; satır bazında parçala.
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": c,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if not r.ok:
            print("Telegram hatası:", r.text, file=sys.stderr)


def fmt(it):
    return f'• <a href="{html.escape(it["link"] or "")}">{html.escape(it["title"])}</a>'


def run_alert():
    first_run = not SEEN_FILE.exists()
    seen = set(json.loads(SEEN_FILE.read_text())) if not first_run else set()
    new_items = [it for it in fetch_all() if it["id"] and it["id"] not in seen]

    to_send = []
    if not first_run:
        for it in new_items:
            haystack = (it["title"] + " " + it["summary"]).lower()
            if any(k in haystack for k in KEYWORDS):
                to_send.append(it)

    for it in to_send[:MAX_ALERTS_PER_RUN]:
        send(f'🚨 <b>{html.escape(it["source"])}</b>\n{fmt(it)}')

    seen.update(it["id"] for it in new_items)
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-3000:]))
    print(f"Yeni: {len(new_items)}, gönderilen: {min(len(to_send), MAX_ALERTS_PER_RUN)}")


def run_digest():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    by_source = {}
    for it in fetch_all():
        if it["time"] and it["time"] >= cutoff:
            by_source.setdefault(it["source"], []).append(it)

    if not by_source:
        send("📰 Son 24 saatte kaynaklardan yeni içerik alınamadı.")
        return

    today = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    parts = [f"📰 <b>Orta Doğu Günlük Özet – {today}</b>"]
    for source, its in by_source.items():
        its.sort(key=lambda x: x["time"], reverse=True)
        parts.append(f"\n<b>{html.escape(source)}</b>")
        parts.extend(fmt(it) for it in its[:DIGEST_PER_SOURCE])
    send("\n".join(parts))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "alert"
    {"alert": run_alert, "digest": run_digest}[mode]()
