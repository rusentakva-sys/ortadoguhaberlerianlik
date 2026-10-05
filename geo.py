"""Haber metinlerinden olay türü ve yaklaşık konum çıkarma.

Konumlar, metinde geçen yer adlarının aşağıdaki sözlükle eşleştirilmesiyle bulunur.
Koordinatlar yaklaşıktır (şehir/ilçe merkezi veya tesis çevresi). Haber metni sokak
düzeyinde konum vermediği sürece bundan daha kesin bir konum üretilemez.

kind: "site" = tesis/üs, "city" = şehir/ilçe, "area" = geniş bölge (belirsiz, haritada daire olarak gösterilir)
"""
import re

# (ad, enlem, boylam, tür, bölge etiketi, [küçük harfli yazım biçimleri])
PLACES = [
    # ── Gazze
    ("Gazze Şeridi", 31.42, 34.35, "area", "Gazze", ["gaza strip", "gazze şeridi", "gaza", "gazze"]),
    ("Gazze Şehri", 31.5017, 34.4668, "city", "Gazze", ["gaza city", "gazze şehri"]),
    ("Han Yunus", 31.3462, 34.3063, "city", "Gazze", ["khan younis", "khan yunis", "khan yunus", "han yunus", "hanyunus"]),
    ("Refah", 31.2969, 34.2435, "city", "Gazze", ["rafah", "refah"]),
    ("Deyrülbelah", 31.4180, 34.3503, "city", "Gazze",
     ["deir al-balah", "deir el-balah", "deir al balah", "deir albalah", "deyrülbelah", "deyr el-belah"]),
    ("Cebaliya", 31.5272, 34.4836, "city", "Gazze", ["jabalia", "jabaliya", "cebaliya"]),
    ("Beyt Lahiya", 31.5456, 34.5008, "city", "Gazze", ["beit lahia", "beit lahiya", "beit lahya", "beyt lahiya"]),
    ("Beyt Hanun", 31.5367, 34.5339, "city", "Gazze", ["beit hanoun", "beit hanun", "beyt hanun"]),
    ("Nuseyrat", 31.4470, 34.3922, "city", "Gazze", ["nuseirat", "nusseirat", "nuseyrat"]),
    ("Şuceiyye", 31.4986, 34.4800, "city", "Gazze", ["shujayea", "shejaiya", "shuja'iyya", "shujaiya", "şuceyye"]),
    ("El-Mevasi", 31.3300, 34.2300, "area", "Gazze", ["al-mawasi", "al mawasi", "mawasi", "muvasi"]),
    # ── Batı Şeria ve Kudüs
    ("Batı Şeria", 31.95, 35.25, "area", "Batı Şeria", ["west bank", "batı şeria"]),
    ("Cenin", 32.4610, 35.2999, "city", "Batı Şeria", ["jenin", "cenin"]),
    ("Nablus", 32.2211, 35.2544, "city", "Batı Şeria", ["nablus"]),
    ("Tulkerm", 32.3104, 35.0286, "city", "Batı Şeria", ["tulkarm", "tulkarem", "tulkerm"]),
    ("Ramallah", 31.9038, 35.2034, "city", "Batı Şeria", ["ramallah"]),
    ("El-Halil", 31.5326, 35.0998, "city", "Batı Şeria", ["hebron", "el-halil"]),
    ("Beytüllahim", 31.7054, 35.2024, "city", "Batı Şeria", ["bethlehem", "beytüllahim"]),
    ("Eriha", 31.8667, 35.4500, "city", "Batı Şeria", ["jericho", "eriha"]),
    ("Tubas", 32.3209, 35.3697, "city", "Batı Şeria", ["tubas"]),
    ("Kalkilya", 32.1896, 34.9706, "city", "Batı Şeria", ["qalqilya", "qalqilyah", "kalkilya"]),
    ("Kudüs", 31.7683, 35.2137, "city", "Kudüs", ["east jerusalem", "jerusalem", "doğu kudüs", "kudüs", "al-quds"]),
    # ── İsrail
    ("Tel Aviv", 32.0853, 34.7818, "city", "İsrail", ["tel aviv", "tel-aviv"]),
    ("Hayfa", 32.7940, 34.9896, "city", "İsrail", ["haifa", "hayfa"]),
    ("Beerşeba", 31.2530, 34.7915, "city", "İsrail", ["beersheba", "beer sheva", "beer-sheva", "beerşeba"]),
    ("Aşkelon", 31.6688, 34.5743, "city", "İsrail", ["ashkelon", "aşkelon"]),
    ("Aşdod", 31.8040, 34.6553, "city", "İsrail", ["ashdod", "aşdod"]),
    ("Sderot", 31.5250, 34.5967, "city", "İsrail", ["sderot"]),
    ("Eilat", 29.5577, 34.9519, "city", "İsrail", ["eilat", "eylat"]),
    ("Kiryat Şmona", 33.2078, 35.5700, "city", "İsrail", ["kiryat shmona", "kiryat shmonah", "qiryat shemona", "kiryat şmona"]),
    ("Nahariya", 33.0058, 35.0950, "city", "İsrail", ["nahariya", "nahariyya"]),
    ("Taberiye", 32.7959, 35.5300, "city", "İsrail", ["tiberias", "taberiye"]),
    ("Safed", 32.9646, 35.4960, "city", "İsrail", ["safed", "tzfat", "zefat"]),
    ("Metula", 33.2790, 35.5780, "city", "İsrail", ["metula"]),
    ("Netanya", 32.3215, 34.8532, "city", "İsrail", ["netanya"]),
    ("Hadera", 32.4340, 34.9196, "city", "İsrail", ["hadera"]),
    ("Petah Tikva", 32.0840, 34.8878, "city", "İsrail", ["petah tikva", "petah tikvah"]),
    ("Dimona", 31.0700, 35.0300, "city", "İsrail", ["dimona"]),
    ("Ben Gurion Havalimanı", 32.0114, 34.8867, "site", "İsrail",
     ["ben gurion airport", "ben-gurion airport", "ben gurion havalimanı"]),
    ("Nevatim Hava Üssü", 31.2083, 35.0122, "site", "İsrail", ["nevatim"]),
    ("Golan Tepeleri", 33.00, 35.75, "area", "Golan", ["golan heights", "golan tepeleri", "golan"]),
    # ── Lübnan
    ("Beyrut", 33.8938, 35.5018, "city", "Lübnan", ["beirut", "beyrut"]),
    ("Dahiye (Beyrut güney banliyösü)", 33.8486, 35.5090, "city", "Lübnan",
     ["beirut's southern suburbs", "southern suburbs of beirut", "dahieh", "dahiyeh", "dahiya", "dahiye"]),
    ("Sur", 33.2705, 35.2038, "city", "Lübnan", ["tyre"]),
    ("Sayda", 33.5630, 35.3688, "city", "Lübnan", ["sidon", "saida", "sayda"]),
    ("Nebatiye", 33.3772, 35.4836, "city", "Lübnan", ["nabatieh", "nabatiyeh", "nebatiye"]),
    ("Baalbek", 34.0047, 36.2110, "city", "Lübnan", ["baalbek", "baalbeck"]),
    ("Bint Cbeyl", 33.1167, 35.4333, "city", "Lübnan", ["bint jbeil", "bint jbail", "bint cbeyl"]),
    ("Marcayun", 33.3600, 35.5900, "city", "Lübnan", ["marjayoun", "marjaayoun", "marcayun"]),
    ("Hermel", 34.3944, 36.3856, "city", "Lübnan", ["hermel"]),
    ("Zahle", 33.8463, 35.9020, "city", "Lübnan", ["zahle", "zahleh", "zahlé"]),
    ("Nakura", 33.1180, 35.1400, "city", "Lübnan", ["naqoura", "nakura"]),
    ("Hayyam", 33.3333, 35.6000, "city", "Lübnan", ["khiam", "al-khiam", "hayyam"]),
    ("Güney Lübnan", 33.20, 35.45, "area", "Lübnan", ["southern lebanon", "south lebanon", "güney lübnan"]),
    ("Bekaa Vadisi", 33.85, 36.00, "area", "Lübnan", ["bekaa valley", "bekaa", "beqaa", "beka vadisi"]),
    # ── Suriye
    ("Şam", 33.5138, 36.2765, "city", "Suriye", ["damascus", "şam", "dimashq"]),
    ("Halep", 36.2021, 37.1343, "city", "Suriye", ["aleppo", "halep"]),
    ("Humus", 34.7324, 36.7137, "city", "Suriye", ["homs", "humus"]),
    ("Hama", 35.1318, 36.7578, "city", "Suriye", ["hamah", "hama"]),
    ("İdlib", 35.9306, 36.6339, "city", "Suriye", ["idlib", "idleb"]),
    ("Lazkiye", 35.5317, 35.7907, "city", "Suriye", ["latakia", "lattakia", "lazkiye"]),
    ("Tartus", 34.8890, 35.8866, "city", "Suriye", ["tartus", "tartous"]),
    ("Deyrizor", 35.3359, 40.1408, "city", "Suriye",
     ["deir ez-zor", "deir ezzor", "deir al-zour", "deir ez zor", "deir el-zour", "deyrizor"]),
    ("Rakka", 35.9528, 39.0079, "city", "Suriye", ["raqqa", "raqqah", "rakka"]),
    ("Dera", 32.6189, 36.1021, "city", "Suriye", ["daraa", "deraa"]),
    ("Kuneytra", 33.1257, 35.8243, "city", "Suriye", ["quneitra", "kuneitra", "kuneytra"]),
    ("Süveyda", 32.7090, 36.5695, "city", "Suriye", ["as-suwayda", "suwayda", "sweida", "suweida", "süveyda"]),
    ("Kamışlı", 37.0522, 41.2314, "city", "Suriye", ["qamishli", "qamishlo", "kamışlı"]),
    ("Haseke", 36.5024, 40.7477, "city", "Suriye", ["al-hasakah", "hasakah", "hasaka", "haseke"]),
    ("Palmira", 34.5600, 38.2700, "city", "Suriye", ["palmyra", "tadmur", "palmira"]),
    ("Münbiç", 36.5281, 37.9544, "city", "Suriye", ["manbij", "münbiç"]),
    ("Kobani", 36.8900, 38.3550, "city", "Suriye", ["kobani", "kobane", "ayn al-arab"]),
    ("Cerablus", 36.8200, 38.0100, "city", "Suriye", ["jarabulus", "cerablus"]),
    ("Afrin", 36.5120, 36.8700, "city", "Suriye", ["afrin"]),
    ("Resulayn", 36.8500, 40.0700, "city", "Suriye", ["ras al-ain", "ras al ain", "resulayn"]),
    ("Tel Abyad", 36.6980, 38.9500, "city", "Suriye", ["tal abyad", "tel abyad"]),
    ("Et-Tanf Üssü", 33.5167, 38.6667, "site", "Suriye", ["al-tanf", "et-tanf", "tanf"]),
    ("Hmeymim Üssü", 35.4011, 35.9486, "site", "Suriye", ["hmeimim", "khmeimim", "hmeymim"]),
    # ── Irak
    ("Bağdat", 33.3152, 44.3661, "city", "Irak", ["baghdad", "bağdat"]),
    ("Erbil", 36.1911, 44.0092, "city", "Irak", ["erbil", "irbil", "arbil"]),
    ("Musul", 36.3400, 43.1300, "city", "Irak", ["mosul", "musul"]),
    ("Basra", 30.5085, 47.7836, "city", "Irak", ["basra", "basrah"]),
    ("Kerkük", 35.4681, 44.3922, "city", "Irak", ["kirkuk", "kerkük"]),
    ("Süleymaniye", 35.5558, 45.4351, "city", "Irak", ["sulaymaniyah", "sulaimani", "sulaymaniya", "süleymaniye"]),
    ("Necef", 32.0000, 44.3333, "city", "Irak", ["najaf", "necef"]),
    ("Kerbela", 32.6160, 44.0249, "city", "Irak", ["karbala", "kerbela"]),
    ("Felluce", 33.3500, 43.7833, "city", "Irak", ["fallujah", "felluce"]),
    ("Ramadi", 33.4258, 43.2996, "city", "Irak", ["ramadi"]),
    ("Sincar", 36.3200, 41.8700, "city", "Irak", ["sinjar", "sincar"]),
    ("Duhok", 36.8669, 42.9503, "city", "Irak", ["duhok", "dohuk", "dahuk"]),
    ("Tikrit", 34.6000, 43.6833, "city", "Irak", ["tikrit"]),
    ("Ayn el-Esed Hava Üssü", 33.7856, 42.4411, "site", "Irak",
     ["ain al-asad", "ain al asad", "ayn al-asad", "al-asad air base", "ayn el-esed"]),
    # ── İran
    ("Tahran", 35.6892, 51.3890, "city", "İran", ["tehran", "tahran"]),
    ("İsfahan", 32.6546, 51.6680, "city", "İran", ["isfahan", "esfahan"]),
    ("Natanz Nükleer Tesisi", 33.7243, 51.7275, "site", "İran", ["natanz"]),
    ("Fordo Nükleer Tesisi", 34.8850, 50.9960, "site", "İran", ["fordow", "fordo", "fordu"]),
    ("Parçin Askeri Kompleksi", 35.5200, 51.7700, "site", "İran", ["parchin", "parçin"]),
    ("Buşehr", 28.9234, 50.8203, "city", "İran", ["bushehr", "buşehr"]),
    ("Bender Abbas", 27.1865, 56.2808, "city", "İran", ["bandar abbas", "bender abbas"]),
    ("Tebriz", 38.0962, 46.2738, "city", "İran", ["tabriz", "tebriz"]),
    ("Şiraz", 29.5918, 52.5837, "city", "İran", ["shiraz", "şiraz"]),
    ("Meşhed", 36.2605, 59.6168, "city", "İran", ["mashhad", "meşhed"]),
    ("Kirmanşah", 34.3277, 47.0778, "city", "İran", ["kermanshah", "kirmanşah"]),
    ("Ahvaz", 31.3183, 48.6706, "city", "İran", ["ahvaz", "ahwaz"]),
    ("Kerec", 35.8400, 50.9391, "city", "İran", ["karaj", "kerec"]),
    ("Çabahar", 25.2919, 60.6430, "city", "İran", ["chabahar", "çabahar"]),
    ("Zahedan", 29.4963, 60.8629, "city", "İran", ["zahedan", "zahidan"]),
    ("Abadan", 30.3392, 48.3043, "city", "İran", ["abadan"]),
    ("Hamedan", 34.7989, 48.5150, "city", "İran", ["hamadan", "hamedan"]),
    ("Urmiye", 37.5527, 45.0760, "city", "İran", ["urmia", "urmiye"]),
    ("Hark Adası", 29.2333, 50.3167, "site", "İran", ["kharg island", "kharg", "hark adası"]),
    ("Hürmüz Boğazı", 26.5667, 56.2500, "area", "Hürmüz", ["strait of hormuz", "hormuz", "hürmüz boğazı", "hürmüz"]),
    # ── Yemen
    ("Sana", 15.3694, 44.1910, "city", "Yemen",
     ["sanaa", "sana'a", "sana'da", "sana'ya", "sana'nın", "sana'dan"]),
    ("Hudeyde", 14.7978, 42.9545, "city", "Yemen",
     ["al hudaydah", "hodeidah", "hudaydah", "hodeida", "hudaida", "hudeyde"]),
    ("Aden", 12.7855, 45.0187, "city", "Yemen", ["aden"]),
    ("Sada", 16.9400, 43.7600, "city", "Yemen", ["saada", "sa'ada", "saadah"]),
    ("Marib", 15.4590, 45.3222, "city", "Yemen", ["marib", "ma'rib", "mareb"]),
    ("Taiz", 13.5789, 44.0219, "city", "Yemen", ["taiz", "ta'izz", "taizz"]),
    ("Babülmendep Boğazı", 12.5833, 43.3333, "area", "Yemen",
     ["bab el-mandeb", "bab al-mandab", "bab-el-mandeb", "bab el mandeb", "babülmendep"]),
    # ── Ürdün, Mısır
    ("Amman", 31.9454, 35.9284, "city", "Ürdün", ["amman"]),
    ("Akabe", 29.5321, 35.0063, "city", "Ürdün", ["aqaba", "akabe"]),
    ("Kahire", 30.0444, 31.2357, "city", "Mısır", ["cairo", "kahire"]),
    ("El-Ariş", 31.1316, 33.7984, "city", "Mısır", ["el-arish", "al-arish", "el arish", "arish"]),
    ("Şarm el-Şeyh", 27.9158, 34.3300, "city", "Mısır", ["sharm el-sheikh", "sharm el sheikh", "şarm el-şeyh"]),
    # ── Türkiye
    ("Hatay", 36.4018, 36.3498, "city", "Türkiye", ["hatay"]),
    ("Diyarbakır", 37.9144, 40.2306, "city", "Türkiye", ["diyarbakır", "diyarbakir"]),
    ("Şırnak", 37.5164, 42.4611, "city", "Türkiye", ["şırnak", "sirnak"]),
    ("Hakkari", 37.5744, 43.7408, "city", "Türkiye", ["hakkari", "hakkâri"]),
    ("Gaziantep", 37.0662, 37.3833, "city", "Türkiye", ["gaziantep"]),
    ("Şanlıurfa", 37.1591, 38.7969, "city", "Türkiye", ["şanlıurfa", "sanliurfa"]),
    ("Mardin", 37.3212, 40.7245, "city", "Türkiye", ["mardin"]),
    ("Kilis", 36.7184, 37.1212, "city", "Türkiye", ["kilis"]),
    ("Ankara", 39.9334, 32.8597, "city", "Türkiye", ["ankara"]),
    ("İstanbul", 41.0082, 28.9784, "city", "Türkiye", ["istanbul"]),
    # ── Körfez
    ("Riyad", 24.7136, 46.6753, "city", "Suudi Arabistan", ["riyadh", "riyad"]),
    ("Cidde", 21.4858, 39.1925, "city", "Suudi Arabistan", ["jeddah", "cidde"]),
    ("Dahran", 26.2361, 50.0393, "city", "Suudi Arabistan", ["dhahran", "dahran"]),
    ("Ras Tanura", 26.6444, 50.1581, "site", "Suudi Arabistan", ["ras tanura"]),
    ("Abkayk", 25.9364, 49.6764, "site", "Suudi Arabistan", ["abqaiq", "buqayq", "abkayk"]),
    ("Cizan", 16.8892, 42.5511, "city", "Suudi Arabistan", ["jazan", "jizan", "cizan"]),
    ("Ebha", 18.2164, 42.5053, "city", "Suudi Arabistan", ["abha"]),
    ("Necran", 17.4917, 44.1322, "city", "Suudi Arabistan", ["najran", "necran"]),
    ("Abu Dabi", 24.4539, 54.3773, "city", "BAE", ["abu dhabi", "abu dabi"]),
    ("Dubai", 25.2048, 55.2708, "city", "BAE", ["dubai"]),
    ("Fucayra", 25.1288, 56.3265, "city", "BAE", ["fujairah", "fucayra"]),
    ("Doha", 25.2854, 51.5310, "city", "Katar", ["doha"]),
    ("El-Udeyd Hava Üssü", 25.1173, 51.3150, "site", "Katar", ["al udeid", "al-udeid", "udeid"]),
    ("Kuveyt", 29.3759, 47.9774, "city", "Kuveyt", ["kuwait city", "kuveyt şehri"]),
    ("Manama", 26.2285, 50.5860, "city", "Bahreyn", ["manama"]),
    ("Maskat", 23.5880, 58.3829, "city", "Umman", ["muscat", "maskat"]),
]

RANK = {"area": 1, "city": 2, "site": 3}


def norm(s):
    return (s or "").replace("İ", "i").replace("’", "'").lower()


_ALIAS = {}
for _i, _p in enumerate(PLACES):
    for _a in _p[5]:
        _ALIAS[norm(_a)] = _i
_ALIAS_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(a) for a in sorted(_ALIAS, key=len, reverse=True)) + r")(?!\w)"
)


def _place(i):
    n, lat, lon, kind, region, _ = PLACES[i]
    return {"place": n, "lat": lat, "lon": lon, "kind": kind, "region": region}


def hits(text):
    """Metindeki sözlük yerlerini, geçiş sırasına göre (tekrarsız) döndürür."""
    seen, out = set(), []
    for m in _ALIAS_RE.finditer(norm(text)):
        i = _ALIAS[m.group(1)]
        if i not in seen:
            seen.add(i)
            out.append(_place(i))
    return out


def pick_places(title, summary="", max_n=2):
    """Başlık ve özetten olayın yaklaşık konumunu seçer (en fazla max_n yer)."""
    th = hits(title)
    spec = [p for p in th if p["kind"] in ("site", "city")]
    if spec:
        return spec[:max_n]
    if th:  # başlıkta yalnızca geniş bölge var: özette aynı bölgeden daha kesin bir yer varsa onu kullan
        region = th[0]["region"]
        better = [p for p in hits(summary) if p["kind"] in ("site", "city") and p["region"] == region]
        return better[:1] if better else th[:1]
    sh = hits(summary)
    spec = [p for p in sh if p["kind"] in ("site", "city")]
    if spec:
        return spec[:1]
    return sh[:1]


# ── Olay türleri (öncelik sırasıyla; ilk eşleşen kazanır)
TYPES = [
    ("suikast", "Suikast", ["assassinat", "targeted killing", "suikast"]),
    ("patlama", "Patlama", ["explosion", "explosive", "blast", "car bomb", "patlama", "patlayıcı", "bombalı"]),
    ("hava", "Hava saldırısı", ["airstrike", "air strike", "air raid", "aerial", "bombard", "bombing", "bombed",
                                 "warplane", "jets struck", "hava saldırı", "hava harekât", "bombardıman"]),
    ("fuze", "Füze / İHA saldırısı", ["missile", "rocket", "drone", "uav", "ballistic", "füze", "roket",
                                       "insansız hava", "iha ", "iha'"]),
    ("mudahale", "Müdahale / harekât", ["ground operation", "incursion", "raid", "stormed", "intervention",
                                         "military operation", "invasion", "invade", "müdahale", "kara harekât",
                                         "baskın", "operasyon", "işgal"]),
    ("catisma", "Saldırı / çatışma", ["clashes", "fighting", "shelling", "shelled", "attack", "strike", "struck",
                                       "killed", "kills", "wounded", "martyred", "saldırı", "çatışma", "topçu", "hayatını kaybetti",
                                       "şehit", "öldü"]),
]
TYPE_LABEL = {k: lbl for k, lbl, _ in TYPES}


def classify(title):
    """Başlıktan olay türünü bulur; olay değilse None."""
    t = norm(title) + " "
    for key, label, words in TYPES:
        if any(norm(w) in t for w in words):
            return key, label
    return None


# ── Sözlükte olmayan yer adları için aday çıkarma (çevrimiçi eşleştirme için)
_STOP = {
    "israel", "iran", "lebanon", "syria", "iraq", "yemen", "gaza", "turkey", "jordan", "egypt", "saudi", "qatar",
    "washington", "trump", "biden", "russia", "china", "ukraine", "europe", "london", "brussels", "paris", "moscow",
    "new", "york", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "the", "a", "an",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "hamas", "hezbollah", "idf", "un", "nato", "eu", "us", "u.s.", "palestinian", "israeli", "iranian",
}
_GENERIC_TAIL = {"village", "city", "town", "province", "district", "governorate", "camp", "area", "region"}
_PHRASE = re.compile(
    r"\b(?:in|near|at|outside|around|south of|north of|east of|west of|into)\s+"
    r"([A-Z][\w'’\-]+(?:\s+(?:[A-Z][\w'’\-]+|al-[\w]+|el-[\w]+)){0,2})"
)


def candidate_names(title):
    out = []
    for m in _PHRASE.finditer(title or ""):
        name = m.group(1).strip()
        parts = name.split()
        while len(parts) > 1 and parts[-1].lower() in _GENERIC_TAIL:
            parts.pop()  # "Zaranj Village" -> "Zaranj"
        name = " ".join(parts)
        if name.lower() in _STOP or name.split()[0].lower() in _STOP:
            continue
        if name not in out:
            out.append(name)
    return out[:2]
