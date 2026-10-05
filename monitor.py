#!/usr/bin/env python3
"""Orta Doğu takip botu -> Telegram (Türkçe, tam metin).

Modlar:
  python monitor.py run      # (Actions, ~10 dk'da bir) önce komutlara bakar, sonra anlık uyarıları tarar
  python monitor.py digest   # günlük gündem raporu (07:00)
  python monitor.py report   # "son durum" raporunu elle üretir
  python monitor.py alert    # sadece anlık uyarı taraması
  python monitor.py poll     # sadece Telegram komutlarına bakar
  python monitor.py events   # sadece olay haritası verisini (docs/events.json) günceller
  python monitor.py check    # kaynakları ve çeviri sağlayıcılarını test eder
  python monitor.py serve    # (sürekli çalışan sunucu için) her şeyi tek süreçte yapar

Ortam değişkenleri:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (zorunlu)
"""
import atexit
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import mktime

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

import geo

# ───────────────────────── Ayarlar ─────────────────────────
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"]).strip()

SEEN_FILE = Path("seen.json")
TR_TZ = timezone(timedelta(hours=3))

# Günlük rapor (07:00) ve "son durum" raporu boyutları. Mesaj sayısı çok artarsa bunları düşür.
DIGEST_HOURS, DIGEST_PER_SOURCE, DIGEST_MAX_ITEMS = 24, 3, 40
ONDEMAND_HOURS, ONDEMAND_PER_SOURCE, ONDEMAND_MAX_ITEMS = 8, 2, 25
MAX_ALERTS_PER_RUN = 10

# Tam metin ayarları. Yalnızca TÜRKÇE kaynaklar için geçerlidir (çeviri gerekmez).
# Türkçe olmayan kaynaklar her zaman özet olarak gelir ve Türkçeye çevrilir.
FULL_TEXT_DIGEST = True     # 07:00 gündem raporu
FULL_TEXT_ONDEMAND = True   # "son durum nedir" raporu
FULL_TEXT_ALERTS = True     # anlık uyarılar
FOREIGN_SUMMARY_CHARS = 500  # Türkçe olmayan haberlerde özet uzunluğu (çeviri yükünü düşük tutar)
FULL_TEXT_MAX_CHARS = 6000  # bundan uzun haberler kısaltılır, sonuna kaynağa yönlendirme eklenir
SHOW_SUMMARY = True         # tam metin alınamazsa özet gösterilir

# Herkese açık Telegram kanalları (kanal adı, @ olmadan). Örn: ["kanal_adi_1", "kanal_adi_2"]
TELEGRAM_CHANNELS = []

# "son durum" demenin yolları (büyük/küçük harf fark etmez)
# Olay haritası (docs/events.json dosyasını docs/index.html okur)
EVENTS_FILE = Path("docs/events.json")
GEOCACHE_FILE = Path("geocache.json")
EVENT_RETENTION_DAYS = 7           # haritada tutulan gün sayısı
EVENT_MERGE_HOURS = 6              # aynı yer + aynı tür olaylar bu süre içinde tek olay sayılır
EVENT_MAX = 1500
EVENT_EXCLUDED_GROUPS = {"Analiz ve düşünce kuruluşları"}  # analizler olay sayılmaz
GEOCODE_ONLINE = True              # sözlükte olmayan yer adlarını OpenStreetMap (Nominatim) ile ara
GEOCODE_MAX_PER_RUN = 8
MAP_URL = os.environ.get("MAP_URL", "").strip()
MAP_WORDS = ["harita", "/harita"]

COMMAND_WORDS = ["son durum", "durum nedir", "gündem", "gundem", "/durum", "/sondurum", "/rapor"]

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# ───────────────────────── Kaynaklar ─────────────────────────
# S(grup, ad, rss_adresi, etiket, tr=Türkçe kaynak mı, flt=sadece bölgeyle ilgili haberleri al,
#   partisan=taraflı/devlet medyası uyarısı göster)
# Adresler zamanla değişebilir. `python monitor.py check` hangilerinin çalıştığını söyler.
SOURCES, GROUPS = [], []


def S(group, name, url, tag, tr=False, flt=False, partisan=False):
    if group not in GROUPS:
        GROUPS.append(group)
    SOURCES.append(dict(group=group, name=name, url=url, tag=tag, tr=tr, flt=flt, partisan=partisan))


G = "Uluslararası ve BM"
S(G, "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "Katar merkezli yayın", flt=True)
S(G, "BBC Orta Doğu", "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "İngiltere kamu yayıncısı")
S(G, "Guardian Orta Doğu", "https://www.theguardian.com/world/middleeast/rss", "İngiltere")
S(G, "BM Haberleri", "https://news.un.org/feed/subscribe/en/news/region/middle-east/feed/rss.xml", "BM (resmi)")

G = "Türkiye"
S(G, "Anadolu Ajansı", "https://www.aa.com.tr/tr/rss/default?cat=dunya", "devlet ajansı", tr=True, flt=True)
S(G, "TRT Haber", "https://www.trthaber.com/dunya_articles.rss", "kamu yayıncısı", tr=True, flt=True)
S(G, "Hürriyet", "https://www.hurriyet.com.tr/rss/dunya", "Türkiye", tr=True, flt=True)
S(G, "Daily Sabah", "https://www.dailysabah.com/rssFeed/world", "hükümete yakın yayın", flt=True)
S(G, "Yeni Şafak", "https://www.yenisafak.com/rss?category=dunya", "hükümete yakın yayın", tr=True, flt=True)

G = "İsrail kaynakları"
S(G, "Jerusalem Post", "https://www.jpost.com/rss/rssfeedsfrontpage.aspx", "İsrail", flt=True)
S(G, "Haaretz", "https://www.haaretz.com/srv/haaretz-latest-headlines", "İsrail", flt=True)
S(G, "Ynetnews", "https://www.ynetnews.com/Integration/StoryRss3082.xml", "İsrail")

G = "Arap dünyası ve Filistin"
S(G, "Middle East Eye", "https://www.middleeasteye.net/rss", "Londra merkezli")
S(G, "Asharq Al-Awsat", "https://english.aawsat.com/feed", "Suudi sahipli yayın", partisan=True)
S(G, "Al-Monitor", "https://www.al-monitor.com/rss", "ABD merkezli analiz")

G = "İran ve direniş ekseni"
S(G, "Press TV", "https://www.presstv.ir/rss.xml", "İran devlet medyası", partisan=True)
S(G, "IRNA", "https://en.irna.ir/rss", "İran devlet ajansı", partisan=True)
S(G, "Tehran Times", "https://www.tehrantimes.com/rss", "İran, hükümete yakın", partisan=True)
S(G, "SANA", "https://sana.sy/en/?feed=rss2", "Suriye devlet ajansı", partisan=True)
S(G, "Al Manar", "https://english.almanar.com.lb/rss", "Hizbullah medyası", partisan=True)

G = "Analiz ve düşünce kuruluşları"
S(G, "Crisis Group", "https://www.crisisgroup.org/rss.xml", "uluslararası düşünce kuruluşu", flt=True)
S(G, "War on the Rocks", "https://warontherocks.com/feed/", "ABD merkezli güvenlik analizi", flt=True)
S(G, "Atlantic Council", "https://www.atlanticcouncil.org/feed/", "ABD merkezli düşünce kuruluşu", flt=True)

# Aşağıdaki kaynaklar GitHub sunucularından 403 (engelli), zaman aşımı veya boş yanıt verdi.
# Kendi bilgisayarında çalıştırırsan işe yarayabilir; denemek için başındaki # işaretini kaldır.
# (Grup değişkeni G için ilgili grup adını yukarıdan tekrar ata.)
# S(G, "Times of Israel", "https://www.timesofisrael.com/feed/", "İsrail")
# S(G, "Israel Hayom", "https://www.israelhayom.com/feed/", "İsrail", flt=True)
# S(G, "Middle East Monitor", "https://www.middleeastmonitor.com/feed/", "Londra merkezli, Filistin yanlısı", partisan=True)
# S(G, "The New Arab", "https://www.newarab.com/rss", "Londra merkezli Arap yayını")
# S(G, "Mondoweiss", "https://mondoweiss.net/feed/", "ABD merkezli, Filistin yanlısı", partisan=True)
# S(G, "Arab News", "https://www.arabnews.com/rss.xml", "Suudi sahipli yayın", flt=True, partisan=True)
# S(G, "Al Arabiya", "https://english.alarabiya.net/tools/rss", "Suudi sahipli yayın", flt=True, partisan=True)
# S(G, "The National", "https://www.thenationalnews.com/arc/outboundfeeds/rss/category/mena/?outputType=xml",
#   "BAE devlet destekli", partisan=True)
# S(G, "Tasnim", "https://www.tasnimnews.com/en/rss/feed/0/7/0/all-stories", "İran, Devrim Muhafızları bağlantılı", partisan=True)
# S(G, "Iran International", "https://www.iranintl.com/en/rss", "İran dışı muhalif yayın", partisan=True)
# S(G, "Al Mayadeen", "https://english.almayadeen.net/rss", "Lübnan merkezli, direniş ekseni çizgisi", partisan=True)
# S(G, "ISW", "https://www.understandingwar.org/rss.xml", "ABD merkezli askeri analiz", flt=True)

GROUP_TG = "Sosyal medya (doğrulanmamış)"
if TELEGRAM_CHANNELS:
    GROUPS.append(GROUP_TG)
SRC_INDEX = {s["name"]: i for i, s in enumerate(SOURCES)}

# Bölgeyle ilgili olup olmadığını anlamak için (genel kaynaklardaki haberleri süzer)
KEYWORDS = [
    "israil", "israel", "gazze", "gaza", "hamas", "hizbullah", "hezbollah", "iran", "irak", "iraq",
    "suriye", "syria", "lübnan", "lebanon", "yemen", "husi", "houthi", "filistin", "palestin",
    "batı şeria", "west bank", "orta doğu", "middle east", "kudüs", "jerusalem", "tahran", "tehran",
    "beyrut", "beirut", "damascus", "bağdat", "baghdad", "körfez", "gulf", "suud", "saudi", "katar",
    "qatar", "ürdün", "jordan", "mısır", "egypt", "kızıldeniz", "red sea", "hürmüz", "hormuz",
    "kürt", "kurd", "idf", "netanyahu", "nükleer", "nuclear", "ateşkes", "ceasefire", "iaea", "uaea",
    "arap", "arab",
]
# Anlık bildirimi tetikleyen acil gelişme kelimeleri
ALERT_KEYWORDS = [
    "son dakika", "breaking", "flash", "urgent", "ateşkes", "ceasefire", "saldırı", "attack",
    "airstrike", "hava saldırısı", "strike on", "füze", "missile", "drone", "nükleer", "nuclear",
    "hürmüz", "hormuz", "güvenlik konseyi", "security council", "suikast", "assassinat", "patlama",
    "explosion", "savaş", "war ", "yaptırım", "sanction", "ambargo", "tahliye", "evacuat",
    "işgal", "invasion", "kara harekatı", "ground operation", "rehine", "hostage",
]

FAILED = []  # son taramada okunamayan kaynaklar


def norm(s):
    return (s or "").replace("İ", "i").lower()


# ───────────────────────── Metin yardımcıları ─────────────────────────
def html_to_text(raw):
    t = re.sub(r"(?i)<\s*(/p|br\s*/?|/div|/li|/h[1-6])\s*>", "\n", raw or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"The post .*? appeared first on .*", "", t, flags=re.S)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def clean_text(raw, max_chars=350):
    t = re.sub(r"\s+", " ", html_to_text(raw)).strip()
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return t


def cut_text(text, limit):
    if len(text) <= limit:
        return text, False
    cut = text[:limit].rsplit("\n", 1)[0]
    return (cut if len(cut) > limit // 2 else text[:limit]), True


def entry_time(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime.fromtimestamp(mktime(t), tz=timezone.utc) if t else None


# ───────────────────────── Kaynak okuma ─────────────────────────
def fetch_feed(src):
    r = requests.get(src["url"], headers=UA, timeout=15)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    if not feed.entries:
        raise ValueError("RSS içeriği boş veya RSS değil")
    items = []
    for e in feed.entries:
        link = e.get("link")
        content = ""
        if e.get("content"):
            content = html_to_text(e["content"][0].get("value", ""))
        summary = clean_text(e.get("summary") or e.get("description") or content)
        it = {
            "source": src["name"], "group": src["group"], "tag": src["tag"], "is_tr": src["tr"],
            "partisan": src["partisan"], "filter": src["flt"],
            "id": e.get("id") or link, "title": (e.get("title") or "").strip(),
            "summary": summary, "link": link, "time": entry_time(e),
        }
        if len(content) > 600:
            it["full_text"] = content  # bazı RSS'ler tam metni zaten içerir
        items.append(it)
    return items


def fetch_telegram_channel(channel):
    """Herkese açık Telegram kanalının son paylaşımları (t.me/s/<kanal>)."""
    r = requests.get(f"https://t.me/s/{channel}", headers=UA, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for msg in soup.select("div.tgme_widget_message[data-post]"):
        text_el = msg.select_one(".tgme_widget_message_text")
        date_el = msg.select_one("a.tgme_widget_message_date")
        if not text_el or not date_el:
            continue
        for br in text_el.find_all("br"):
            br.replace_with("\n")
        text = re.sub(r"\n{3,}", "\n\n", text_el.get_text()).strip()
        if not text:
            continue
        link = date_el.get("href")
        ts = None
        time_el = date_el.find("time")
        if time_el and time_el.get("datetime"):
            try:
                ts = datetime.fromisoformat(time_el["datetime"]).astimezone(timezone.utc)
            except ValueError:
                pass
        first = text.split("\n")[0]
        items.append({
            "source": f"Telegram: {channel}", "group": GROUP_TG, "tag": "kanal paylaşımı",
            "is_tr": False, "partisan": False, "filter": True,
            "id": link, "title": first[:120] + ("…" if len(first) > 120 else ""),
            "summary": clean_text(text), "full_text": text, "link": link, "time": ts,
        })
    return items


def fetch_full_text(url):
    if not url:
        return ""
    try:
        r = requests.get(url, headers=UA, timeout=25)
        if not r.ok:
            return ""
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False, favor_precision=True)
        return (text or "").strip()
    except Exception as ex:
        print(f"Tam metin alınamadı ({url}): {ex}", file=sys.stderr)
        return ""


def fetch_all():
    FAILED.clear()
    items = []
    for src in SOURCES:
        try:
            items.extend(fetch_feed(src))
        except Exception as ex:
            FAILED.append(src["name"])
            print(f"UYARI: {src['name']}: {ex}", file=sys.stderr)
    for ch in TELEGRAM_CHANNELS:
        try:
            items.extend(fetch_telegram_channel(ch))
        except Exception as ex:
            FAILED.append(f"Telegram: {ch}")
            print(f"UYARI: Telegram kanalı {ch}: {ex}", file=sys.stderr)
    return items


def relevant(it):
    if not it["filter"]:
        return True
    text = norm(it["title"] + " " + it["summary"])
    return any(k in text for k in KEYWORDS)


def is_urgent(it):
    text = norm(it["title"] + " " + it["summary"]) + " "
    return any(k in text for k in ALERT_KEYWORDS)


# ───────────────────────── Çeviri (yalnızca Google Çeviri) ─────────────────────────
# Metin olduğu gibi çevrilir; yorum, özet veya düzeltme eklenmez. Sağlayıcıların hepsi Google'dır:
#   1) Resmi Cloud Translation API (GOOGLE_API_KEY tanımlıysa; bulut IP'lerinde en güvenilir yol)
#   2) Google'ın ücretsiz uç noktası  3) Google Çeviri web arayüzü (deep-translator)
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()
GOOGLE_MONTHLY_CHAR_LIMIT = int(os.environ.get("GOOGLE_MONTHLY_CHAR_LIMIT", "") or 450000)
USAGE_FILE = Path("usage.json")
_usage = None


def _load_usage():
    global _usage
    if _usage is None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        try:
            data = json.loads(USAGE_FILE.read_text())
        except Exception:
            data = {}
        _usage = data if data.get("month") == month else {"month": month, "chars": 0}
    return _usage


def save_usage():
    if _usage is not None:
        USAGE_FILE.write_text(json.dumps(_usage))


atexit.register(save_usage)

_google = GoogleTranslator(source="auto", target="tr")


def _google_official(text):
    u = _load_usage()
    if u["chars"] + len(text) > GOOGLE_MONTHLY_CHAR_LIMIT:
        raise RuntimeError("aylık ücretsiz karakter sınırına ulaşıldı, resmi API atlanıyor")
    u["chars"] += len(text)  # ücret çıkmaması için isteği saymadan önce say
    r = requests.post(
        "https://translation.googleapis.com/language/translate/v2",
        params={"key": GOOGLE_API_KEY},
        json={"q": text, "target": "tr", "format": "text"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"]["translations"][0]["translatedText"]


def _google_gtx(text):
    r = requests.post(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "auto", "tl": "tr", "dt": "t"},
        data={"q": text}, headers=UA, timeout=30,
    )
    r.raise_for_status()
    return "".join(seg[0] for seg in r.json()[0] if seg and seg[0])


def _google_tr(text):
    return _google.translate(text)


PROVIDERS = []
if GOOGLE_API_KEY:
    PROVIDERS.append(("Google (resmi API)", _google_official))
PROVIDERS += [("Google (ücretsiz)", _google_gtx), ("Google (web)", _google_tr)]

TR_FAIL_COUNT = 0          # çevrilemeyen metin sayısı (rapor sonunda uyarı için)
_cache, _fail_streak, _last_call = {}, {}, {}
MIN_INTERVAL = {"Google (ücretsiz)": 0.6, "Google (web)": 0.6}  # Google'ı art arda isteklerle yormamak için bekleme (sn)


def _looks_untranslated(src, out):
    s, o = src.strip(), out.strip()
    return len(s) > 25 and s == o and not any(ch in s.lower() for ch in "çğıöşü")


def _plausible(src, out):
    if not out or not out.strip():
        return False
    if _looks_untranslated(src, out):
        return False  # sağlayıcı metni hiç çevirmeden geri vermiş
    if len(src) > 200 and not (0.4 <= len(out) / len(src) <= 2.5):
        return False  # kesik ya da şişirilmiş çeviri
    return True


def _translate_once(text):
    """Sağlayıcıları sırayla dener. Başarısız olursa None döner."""
    global TR_FAIL_COUNT
    for name, fn in PROVIDERS:
        if _fail_streak.get(name, 0) >= 5:
            continue  # bu çalışmada art arda 5 kez başarısız olan sağlayıcıyı atla
        for attempt in range(2):
            wait = MIN_INTERVAL.get(name, 0) - (time.time() - _last_call.get(name, 0))
            if wait > 0:
                time.sleep(wait)
            _last_call[name] = time.time()
            try:
                out = fn(text)
                if _plausible(text, out):
                    _fail_streak[name] = 0
                    return out.strip()
                _fail_streak[name] = _fail_streak.get(name, 0) + 1
                print(f"Çeviri sağlayıcısı {name}: sonuç makul değil, sıradakine geçiliyor", file=sys.stderr)
                break
            except Exception as ex:
                _fail_streak[name] = _fail_streak.get(name, 0) + 1
                print(f"Çeviri hatası ({name}, deneme {attempt + 1}): {type(ex).__name__}: {str(ex)[:200]}",
                      file=sys.stderr)
                time.sleep(3)
    TR_FAIL_COUNT += 1
    return None


def translate_text(text, is_tr=False):
    if is_tr or not text:
        return text
    if text in _cache:
        return _cache[text]
    out = _translate_once(text)
    if out is None:
        return text  # çevrilemedi: orijinal metin (başarısızlıklar önbelleğe alınmaz)
    _cache[text] = out
    return out


def translate_long(text, is_tr=False):
    """Uzun metni paragraf sınırlarında parçalayıp çevirir."""
    if is_tr:
        return text
    chunks, cur = [], ""
    for p in (p.strip() for p in text.split("\n") if p.strip()):
        while len(p) > 4000:
            chunks.append(p[:4000])
            p = p[4000:]
        if cur and len(cur) + len(p) + 1 > 4000:
            chunks.append(cur)
            cur = ""
        cur += p + "\n"
    if cur.strip():
        chunks.append(cur)
    return "\n".join(translate_text(c.strip()) for c in chunks)


def translate_item(it, full):
    before = TR_FAIL_COUNT
    _translate_item(it, full)
    it["tr_failed"] = TR_FAIL_COUNT > before and not it["is_tr"]


def _foreign_summary(it):
    """Türkçe olmayan haber için kısa özet metni üretir (çevrilmeden önce)."""
    summ = it["summary"]
    if it.get("full_text"):  # RSS veya Telegram zaten metni vermişse ondan özet al
        return clean_text(it["full_text"], FOREIGN_SUMMARY_CHARS)
    duplicate = norm(summ).startswith(norm(it["title"])[:40])
    if len(summ) < 40 or duplicate:  # özet yok ya da başlığın tekrarı: sayfadan ilk paragrafları al
        body = fetch_full_text(it["link"])
        if body:
            return clean_text(body, FOREIGN_SUMMARY_CHARS)
        return "" if duplicate or len(summ) < 40 else summ
    return summ


def _translate_item(it, full):
    it["title_tr"] = translate_text(it["title"], it["is_tr"])
    it["body_tr"], it["summary_tr"], it["truncated"] = "", "", False
    if it["is_tr"]:
        # Türkçe kaynaklar: haberin tam metni, çeviri gerekmez
        summ = it["summary"]
        if full:
            body = it.get("full_text") or fetch_full_text(it["link"])
            if body and len(body) > len(summ) + 100:
                it["body_tr"], it["truncated"] = cut_text(body, FULL_TEXT_MAX_CHARS)
        if not it["body_tr"] and SHOW_SUMMARY and len(summ) > 40 \
                and not norm(summ).startswith(norm(it["title"])[:40]):
            it["summary_tr"] = summ
    elif SHOW_SUMMARY:
        # Türkçe olmayan kaynaklar: yalnızca özet, Türkçeye çevrilir
        summ = _foreign_summary(it)
        if summ:
            it["summary_tr"] = translate_text(summ, False)


# ───────────────────────── Telegram ─────────────────────────
def tg_api(method, http_timeout=40, **params):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=params, timeout=http_timeout)
    return r.json()


def _post_message(text, parse_mode):
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    for _ in range(3):
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)) + 1)
            continue
        return r
    return r


def send(text):
    lines = []
    for line in text.split("\n"):
        while len(line) > 3800:
            cut = line.rfind(" ", 0, 3800)
            cut = cut if cut > 0 else 3800
            lines.append(line[:cut])
            line = line[cut:].lstrip()
        lines.append(line)
    chunks, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        r = _post_message(c, "HTML")
        if not r.ok:  # biçim hatasında düz metin olarak yeniden dene
            r = _post_message(re.sub(r"<[^>]+>", "", html.unescape(c)), None)
            if not r.ok:
                print("Telegram hatası:", r.text, file=sys.stderr)
        time.sleep(1.1)  # Telegram hız sınırı


def fmt(it):
    title = it.get("title_tr") or it["title"]
    when = it["time"].astimezone(TR_TZ).strftime("%d.%m %H:%M") if it.get("time") else ""
    label = html.escape(it["source"]) + (f" · {html.escape(it['tag'])}" if it.get("tag") else "")
    lines = [f"🔹 <b>{html.escape(title)}</b>",
             f"🏷 {'⚠️ ' if it.get('partisan') else ''}{label}" + (f" · {when}" if when else "")]
    if it.get("body_tr"):
        lines += ["", html.escape(it["body_tr"])]
        if it.get("truncated"):
            lines.append("<i>(Metin uzun olduğu için kısaltıldı, devamı için orijinal habere bakın.)</i>")
    elif it.get("summary_tr"):
        prefix = "" if it["is_tr"] else "<b>Özet:</b> "
        lines += ["", prefix + html.escape(it["summary_tr"])]
    if it.get("tr_failed"):
        lines.append("⚠️ Çeviri yapılamadı, metin orijinal dilinde.")
    lines.append(f'🔗 <a href="{html.escape(it["link"] or "")}">Orijinal haber</a>')
    return "\n".join(lines) + "\n"


# ───────────────────────── Raporlar ─────────────────────────
def _order_key(it):
    gi = GROUPS.index(it["group"]) if it["group"] in GROUPS else 99
    ts = it["time"].timestamp() if it.get("time") else 0
    return (gi, SRC_INDEX.get(it["source"], 999), -ts)


def select_items(hours, per_source, max_items):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    buckets = {}
    for it in fetch_all():
        if it["time"] and it["time"] >= cutoff and relevant(it):
            buckets.setdefault(it["source"], []).append(it)
    for b in buckets.values():
        b.sort(key=lambda x: x["time"], reverse=True)
        del b[per_source:]
    picked, rank = [], 0
    while len(picked) < max_items and any(rank < len(b) for b in buckets.values()):
        for b in buckets.values():  # her kaynaktan sırayla al, hiçbiri dışarıda kalmasın
            if rank < len(b) and len(picked) < max_items:
                picked.append(b[rank])
        rank += 1
    return sorted(picked, key=_order_key)


def run_report(title, hours, per_source, max_items, full):
    items = select_items(hours, per_source, max_items)
    today = datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M")
    if not items:
        send(f"📰 <b>{html.escape(title)}</b> – {today}\nSon {hours} saatte kaynaklardan yeni içerik alınamadı.")
        return
    n_src = len({it["source"] for it in items})
    send(f"📰 <b>{html.escape(title)}</b> – {today}\n"
         f"Son {hours} saat · {len(items)} haber · {n_src} kaynak\n"
         f"⚠️ işaretli kaynaklar taraflı veya devlet medyasıdır, propaganda içerebilir."
         + (f'\n🗺 <a href="{html.escape(MAP_URL)}">Olay haritası</a>' if MAP_URL else ""))
    last_group = None
    for it in items:
        translate_item(it, full)
        head = ""
        if it["group"] != last_group:
            head = f"━━━ <b>{html.escape(it['group'])}</b> ━━━\n"
            last_group = it["group"]
        send(head + fmt(it))
    if FAILED:
        send("⚠️ Okunamayan kaynaklar: " + html.escape(", ".join(FAILED)))
    if TR_FAIL_COUNT:
        send(f"⚠️ {TR_FAIL_COUNT} metin çevrilemedi (orijinal dilinde kaldı). "
             "Actions'tan <b>check</b> modunu çalıştırıp çeviri sağlayıcılarının durumuna bak.")


def run_digest():
    run_report("Orta Doğu Günlük Gündem", DIGEST_HOURS, DIGEST_PER_SOURCE, DIGEST_MAX_ITEMS, FULL_TEXT_DIGEST)


def run_ondemand():
    run_report("Orta Doğu Son Durum", ONDEMAND_HOURS, ONDEMAND_PER_SOURCE, ONDEMAND_MAX_ITEMS, FULL_TEXT_ONDEMAND)


def run_alert():
    first_run = not SEEN_FILE.exists()
    seen = set() if first_run else set(json.loads(SEEN_FILE.read_text()))
    all_items = fetch_all()
    new_items = [it for it in all_items if it["id"] and it["id"] not in seen]
    to_send = [] if first_run else [it for it in new_items if relevant(it) and is_urgent(it)]
    to_send = to_send[:MAX_ALERTS_PER_RUN]
    for it in to_send:
        translate_item(it, FULL_TEXT_ALERTS)
        send("🚨 <b>ÖNEMLİ GELİŞME</b>\n" + fmt(it))
    try:
        update_events(all_items)
    except Exception as ex:  # harita hatası uyarıları engellemesin
        print("Harita güncellenemedi:", type(ex).__name__, ex, file=sys.stderr)
    seen.update(it["id"] for it in new_items)
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-5000:]))
    print(f"Yeni: {len(new_items)}, gönderilen: {len(to_send)}, okunamayan kaynak: {len(FAILED)}")


# ───────────────────────── Olay haritası ─────────────────────────
_OK_GEO_TYPES = {"city", "town", "village", "hamlet", "suburb", "neighbourhood", "quarter",
                 "city_district", "borough", "municipality"}


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_events():
    try:
        return json.loads(EVENTS_FILE.read_text(encoding="utf-8")).get("events", [])
    except Exception:
        return []


def save_events(events):
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    events.sort(key=lambda e: e["time"], reverse=True)
    data = {"generated": _iso(datetime.now(timezone.utc)), "count": len(events[:EVENT_MAX]),
            "events": events[:EVENT_MAX]}
    EVENTS_FILE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def geocode_online(name, cache, budget):
    """Sözlükte olmayan bir yer adını Nominatim ile arar (yalnızca Orta Doğu çerçevesi, yalnızca yerleşim türleri)."""
    key = name.lower()
    if key in cache:
        return cache[key]
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": name, "format": "jsonv2", "limit": 1, "viewbox": "25,42,65,10", "bounded": 1,
                    "accept-language": "en"},
            headers={"User-Agent": "OrtaDoguTakip/1.0 (kisisel haber takip botu)"}, timeout=15,
        )
        time.sleep(1.1)  # Nominatim kullanım kuralı: saniyede en fazla 1 istek
        r.raise_for_status()
        res = r.json()
        if res and res[0].get("addresstype") in _OK_GEO_TYPES:
            cache[key] = {"lat": round(float(res[0]["lat"]), 4), "lon": round(float(res[0]["lon"]), 4)}
        else:
            cache[key] = None
        return cache[key]
    except Exception as ex:
        print(f"Konum araması başarısız ({name}): {type(ex).__name__}", file=sys.stderr)
        return None


def update_events(items):
    """Haber akışından saldırı/patlama/müdahale olaylarını ayıklayıp harita verisine ekler."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=EVENT_RETENTION_DAYS)
    events = [e for e in load_events() if _parse_iso(e["time"]) >= cutoff]
    known = {s["link"] for e in events for s in e.get("sources", [])}
    try:
        cache = json.loads(GEOCACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    cache_before = json.dumps(cache, sort_keys=True)
    budget = [GEOCODE_MAX_PER_RUN]
    candidates = sorted(
        (i for i in items if i.get("time") and i["time"] >= cutoff and i["group"] not in EVENT_EXCLUDED_GROUPS
         and i.get("link") and i["link"] not in known),
        key=lambda i: i["time"])
    new_n = merged_n = 0
    for it in candidates:
        typ = geo.classify(it["title"])
        if not typ:
            continue
        places = geo.pick_places(it["title"], it["summary"])
        if not places and GEOCODE_ONLINE and not it["is_tr"]:
            for name in geo.candidate_names(it["title"]):
                g = geocode_online(name, cache, budget)
                if g:
                    places = [{"place": name, "lat": g["lat"], "lon": g["lon"], "kind": "auto", "region": ""}]
                    break
        if not places:
            continue
        src = {"name": it["source"], "tag": it["tag"], "partisan": bool(it["partisan"]),
               "link": it["link"], "time": _iso(it["time"])}
        for pl in places:
            # Aynı yer ve zaman penceresi; tür aynıysa ya da biri genel "saldırı/çatışma" ise aynı olay sayılır
            ev = next((e for e in events if e["place"] == pl["place"]
                       and (e["type"] == typ[0] or "catisma" in (e["type"], typ[0]))
                       and abs((it["time"] - _parse_iso(e["updated"])).total_seconds()) <= EVENT_MERGE_HOURS * 3600),
                      None)
            if ev:
                if ev["type"] == "catisma" and typ[0] != "catisma":
                    ev["type"], ev["type_tr"] = typ  # daha belirgin tür bulundu
                ev["sources"].append(src)
                ev["updated"] = max(ev["updated"], src["time"])
                merged_n += 1
                continue
            before = TR_FAIL_COUNT
            title_tr = translate_text(it["title"], it["is_tr"])
            events.append({
                "id": hashlib.sha1((it["link"] + pl["place"]).encode()).hexdigest()[:10],
                "time": src["time"], "updated": src["time"],
                "type": typ[0], "type_tr": typ[1],
                "place": pl["place"], "region": pl["region"], "lat": pl["lat"], "lon": pl["lon"],
                "precision": pl["kind"],
                "title": it["title"], "title_tr": title_tr, "tr_ok": TR_FAIL_COUNT == before or it["is_tr"],
                "sources": [src],
            })
            new_n += 1
        known.add(it["link"])
    if new_n or merged_n or len(events) != len(load_events()):
        save_events(events)
    if json.dumps(cache, sort_keys=True) != cache_before:
        GEOCACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"Harita: {new_n} yeni olay, {merged_n} birleştirilen kaynak, toplam {len(events)}")
    return new_n


def run_events():
    update_events(fetch_all())


def send_map_info():
    if not MAP_URL:
        send("Harita adresi henüz tanımlı değil (MAP_URL).")
        return
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    n = sum(1 for e in load_events() if _parse_iso(e["updated"]) >= cutoff)
    send(f'🗺 <a href="{html.escape(MAP_URL)}">Olay haritasını aç</a>\nSon 24 saatte haritada {n} olay var.')


# ───────────────────────── Komutlar ("son durum nedir") ─────────────────────────
def handle_updates(updates):
    wanted, other, want_map = False, False, False
    for u in updates:
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != CHAT_ID:
            continue  # sadece senin sohbetin; başkalarının mesajları yok sayılır
        text = norm(m.get("text"))
        if not text:
            continue
        if any(w in text for w in COMMAND_WORDS):
            wanted = True
        elif any(w in text for w in MAP_WORDS):
            want_map = True
        else:
            other = True
    if wanted:
        send("⏳ Kaynaklar güncelleniyor ve Türkçeye çevriliyor, birkaç dakika sürebilir...")
        run_ondemand()
    elif want_map:
        send_map_info()
    elif other:
        send("Güncel rapor için <b>son durum nedir</b>, olay haritası için <b>harita</b> yazman yeterli. "
             "Her sabah 07:00'de gündemi kendiliğimden gönderirim.")


def run_poll():
    data = tg_api("getUpdates", timeout=0, allowed_updates=["message"])
    updates = data.get("result", [])
    if not updates:
        return
    last = max(u["update_id"] for u in updates)
    tg_api("getUpdates", offset=last + 1, timeout=0)  # okundu olarak işaretle, tekrar işlenmesin
    handle_updates(updates)


def serve():
    """Sürekli çalışan sunucuda: anında cevap + 10 dk'da bir uyarı + 07:00 raporu."""
    send("🤖 Bot çalışıyor. Güncel rapor için <b>son durum nedir</b> yazabilirsin.")
    offset, last_alert, digest_day = None, 0.0, None
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset
            updates = tg_api("getUpdates", http_timeout=70, **params).get("result", [])
            if updates:
                offset = max(u["update_id"] for u in updates) + 1
                handle_updates(updates)
        except Exception as ex:
            print("serve hatası:", type(ex).__name__, file=sys.stderr)
            time.sleep(5)
        now = datetime.now(TR_TZ)
        if time.time() - last_alert > 600:
            run_alert()
            last_alert = time.time()
        if now.hour == 7 and digest_day != now.date():
            run_digest()
            digest_day = now.date()


# ───────────────────────── Sistem kontrolü ─────────────────────────
def run_check():
    lines = ["🔧 <b>Sistem kontrolü</b>", "", "<b>Kaynaklar</b>"]
    for src in SOURCES:
        try:
            lines.append(f"✅ {html.escape(src['name'])} ({len(fetch_feed(src))} haber)")
        except Exception as ex:
            lines.append(f"❌ {html.escape(src['name'])}: {html.escape(str(ex)[:70])}")
    for ch in TELEGRAM_CHANNELS:
        try:
            lines.append(f"✅ Telegram: {html.escape(ch)} ({len(fetch_telegram_channel(ch))} paylaşım)")
        except Exception as ex:
            lines.append(f"❌ Telegram: {html.escape(ch)}: {html.escape(str(ex)[:70])}")
    lines += ["", "<b>Çeviri (Google)</b>"]
    sample = "The ceasefire talks resumed on Sunday, officials said."
    for name, fn in PROVIDERS:
        try:
            lines.append(f"✅ {name}: {html.escape(fn(sample))}")
        except Exception as ex:
            lines.append(f"❌ {name}: {html.escape(type(ex).__name__ + ': ' + str(ex)[:90])}")
    if GOOGLE_API_KEY:
        u = _load_usage()
        lines.append(f"ℹ️ Resmi API bu ay: {u['chars']:,} / {GOOGLE_MONTHLY_CHAR_LIMIT:,} karakter")
    else:
        lines.append("ℹ️ GOOGLE_API_KEY tanımlı değil; yalnızca ücretsiz Google uç noktaları deneniyor")
    send("\n".join(lines))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "run":
        run_poll()
        run_alert()
    else:
        {"alert": run_alert, "digest": run_digest, "report": run_ondemand, "poll": run_poll,
         "check": run_check, "serve": serve, "events": run_events}[mode]()
