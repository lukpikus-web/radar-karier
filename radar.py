# -*- coding: utf-8 -*-
"""
Radar Karier - silnik: pobieranie ofert i filtry.

Ten plik nie ma okna - uzywa go "Radar Karier.pyw".
Nie wymaga instalowania zadnych bibliotek - tylko Python 3.8+.
"""

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# USTAWIENIA
# Frazy, filtry i lokalizacje ustawia sie w aplikacji: przycisk "Ustawienia".
# Nowa instalacja zaczyna bez zadnych fraz i filtrow.
# Ponizsze listy wypelnia zastosuj_ustawienia() z ustawien zapisanych w radar.db.
# ---------------------------------------------------------------------------

KEYWORDS = []            # pracuj.pl: (fraza, kategoria)
KEYWORDS_LINKEDIN = []   # LinkedIn: (fraza, kategoria)
KEYWORDS_ROCKET = []     # RocketJobs: (fraza, kategoria)
KEYWORDS_OLX = []        # OLX: (fraza, kategoria)
KEYWORDS_USEME = []      # Useme: fraza
STOP_TYTUL = []          # slowa w tytule, przy ktorych oferta odpada
STOP_JEZYK = []          # jezyki, przy ktorych oferta odpada
MIASTO = ""              # np. "warszawa" (adres pracuj.pl); "" = cala Polska
MIASTO_NAZWA = ""
OKOLICE = []
ZDALNE = True
POZIOMY_OK = set()

# WorkConnect - publiczne podglady kategorii marketingu i sprzedazy
# (kazda pokazuje 6 najnowszych zlecen; frazy tu nie dzialaja)
WORKCONNECT_STRONY = [
    "/zlecenia",
    "/zlecenia/marketing-i-sprzedaz",
    "/zlecenia/marketing-i-sprzedaz/marketing-cyfrowy",
    "/zlecenia/marketing-i-sprzedaz/marketing-cyfrowy/sem-reklamy-platne",
    "/zlecenia/marketing-i-sprzedaz/social-media-i-influencer-marketing",
    "/zlecenia/marketing-i-sprzedaz/sprzedaz-i-obsluga-klienta",
]

WLACZONE_ZRODLA = {
    "pracuj.pl": True,
    "LinkedIn": True,
    "RocketJobs": True,
    "OLX": True,
    "Useme": True,
    "WorkConnect": False,    # stale kategorie marketingowe - wlacza sie w Ustawieniach
}

# pracuj.pl pokazuje 50 ofert na strone - ile stron brac na jedna fraze
PRACUJ_MAX_STRON = 3

# Poziomy stanowisk (pracuj.pl). Kolejnosc ma znaczenie: "Mlodszy specjalista"
# ma trafic do "Junior", a nie do "Specjalista", wiec ogolne grupy sa na koncu.
POZIOMY = [
    ("praktykant",  "Praktykant / stażysta",         ["praktykant", "stazyst", "staz", "intern", "trainee"]),
    ("asystent",    "Asystent",                      ["asystent"]),
    ("junior",      "Junior / młodszy specjalista",  ["junior", "mlodszy", "mlodsza"]),
    ("senior",      "Senior / starszy specjalista",  ["senior", "starszy", "starsza"]),
    ("ekspert",     "Ekspert",                       ["ekspert", "expert"]),
    ("kierownik",   "Kierownik / koordynator",       ["kierownik", "kierowniczka", "koordynator", "lead"]),
    ("menedzer",    "Menedżer",                      ["menedzer", "menadzer", "manager"]),
    ("dyrektor",    "Dyrektor / zarząd",             ["dyrektor", "director", "prezes", "zarzad"]),
    ("fizyczny",    "Pracownik fizyczny",            ["fizyczn"]),
    ("specjalista", "Specjalista (mid / regular)",   ["specjalist", "mid", "regular"]),
]

ZRODLA = ["pracuj.pl", "LinkedIn", "RocketJobs", "OLX", "Useme", "WorkConnect"]
ZRODLA_FRAZ = ["pracuj.pl", "LinkedIn", "RocketJobs", "OLX", "Useme"]   # WorkConnect: stale kategorie

PRZERWA = 0.8            # sekundy miedzy zapytaniami

# LinkedIn nie lubi automatow - pytamy go rzadko i grzecznie:
PRZERWA_LINKEDIN = 6         # sekundy miedzy zapytaniami do LinkedIn
LINKEDIN_MAX_ZAPYTAN = 12    # najwyzej tyle zapytan do LinkedIn na jedno odswiezenie
LINKEDIN_CO_ILE_GODZIN = 2   # LinkedIn najwyzej raz na tyle godzin
LINKEDIN_PAUZA_GODZIN = 6    # po odmowie (blad 429/999) - tyle godzin przerwy
KODY_ODMOWY = (429, 999)     # "za duzo zapytan" / blokada LinkedIn
TIMEOUT = 25

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
# Pobieranie stron
# ---------------------------------------------------------------------------

def pobierz(url, proby=3, accept=None):
    """Zwraca tresc strony. Przy bledzie 429/503 odczekuje i probuje ponownie."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        "Connection": "close",
    })
    ctx = ssl.create_default_context()
    czekaj = 8
    for proba in range(proby):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and proba < proby - 1:
                time.sleep(czekaj)
                czekaj *= 2
                continue
            raise


# ---------------------------------------------------------------------------
# Prosty parser HTML - wyciaga bloki po nazwie klasy
# ---------------------------------------------------------------------------

PUSTE_TAGI = {"area", "base", "br", "col", "embed", "hr", "img", "input",
              "link", "meta", "param", "source", "track", "wbr"}


class Bloki(HTMLParser):
    """Znajduje elementy o podanej klasie i zbiera ich zawartosc.

    Trzyma stos otwartych tagow, wiec radzi sobie z niezamknietymi
    znacznikami (np. <p> bez </p>): zamkniecie rodzica domyka tez dzieci.
    """

    def __init__(self, klasa_bloku):
        super().__init__(convert_charrefs=True)
        self.klasa_bloku = klasa_bloku
        self.stos = []
        self.aktywny = None
        self.wyniki = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in PUSTE_TAGI:
            self.handle_startendtag(tag, attrs)
            return
        el = None
        if self.aktywny is None:
            if self.klasa_bloku in (a.get("class") or "").split():
                self.aktywny = {"start": len(self.stos), "attrs": a, "elementy": []}
        else:
            el = {"tag": tag, "attrs": a, "tekst": []}
            self.aktywny["elementy"].append(el)
        self.stos.append({"tag": tag, "el": el})

    def handle_startendtag(self, tag, attrs):
        if self.aktywny is not None:
            self.aktywny["elementy"].append(
                {"tag": tag, "attrs": dict(attrs), "tekst": []})

    def handle_data(self, data):
        if self.aktywny is None or not data.strip():
            return
        for poz in self.stos[self.aktywny["start"]:]:
            if poz["el"] is not None:
                poz["el"]["tekst"].append(data)

    def handle_endtag(self, tag):
        if tag in PUSTE_TAGI:
            return
        indeks = None
        for i in range(len(self.stos) - 1, -1, -1):
            if self.stos[i]["tag"] == tag:
                indeks = i
                break
        if indeks is None:
            return
        zamykamy_blok = self.aktywny is not None and indeks <= self.aktywny["start"]
        del self.stos[indeks:]
        if zamykamy_blok:
            self.wyniki.append(self.aktywny)
            self.aktywny = None

    def close(self):
        super().close()
        if self.aktywny is not None:
            self.wyniki.append(self.aktywny)
            self.aktywny = None


def bloki_po_klasie(html, klasa):
    p = Bloki(klasa)
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    return p.wyniki


def tekst_klasy(blok, klasa):
    for el in blok["elementy"]:
        if klasa in (el["attrs"].get("class") or "").split():
            return " ".join("".join(el["tekst"]).split())
    return ""


def atrybut_klasy(blok, klasa, nazwa):
    for el in blok["elementy"]:
        if klasa in (el["attrs"].get("class") or "").split():
            return el["attrs"].get(nazwa, "")
    return ""


def atrybut_tagu(blok, tag, nazwa):
    for el in blok["elementy"]:
        if el["tag"] == tag:
            return el["attrs"].get(nazwa, "")
    return ""


def link_po_prefiksie(blok, prefiks):
    for el in blok["elementy"]:
        if el["tag"] == "a":
            href = el["attrs"].get("href", "")
            if href.startswith(prefiks):
                return href, " ".join("".join(el["tekst"]).split())
    return "", ""


# ---------------------------------------------------------------------------
# Lokalizacja i dopasowanie do profilu
# ---------------------------------------------------------------------------

OGONKI = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def uprosc(tekst):
    return (tekst or "").translate(OGONKI).lower()


# ---------------------------------------------------------------------------
# Ustawienia uzytkownika (zapisywane w radar.db, edytowane w aplikacji)
# ---------------------------------------------------------------------------

def domyslne_ustawienia():
    """Nowa instalacja: bez fraz i bez filtrow (wszystkie poziomy, cala Polska)."""
    return {
        "frazy": [],
        "zrodla": dict(_ZRODLA_DOMYSLNIE),
        "miasto": "",
        "okolice": [],
        "zdalne": True,
        "stop_tytul": [],
        "stop_jezyk": [],
        "poziomy": [k for k, _, _ in POZIOMY],
        "wersja": 2,
        "cv_klucz": "",          # z jakiego CV sa frazy "Z CV" (zeby nie dodawac ich w kolko)
    }


_ZRODLA_DOMYSLNIE = dict(WLACZONE_ZRODLA)


def uzupelnij_ustawienia(u):
    """Brakujace klucze (np. ze starszej wersji) biora wartosci domyslne."""
    d = domyslne_ustawienia()
    if not isinstance(u, dict):
        return d
    wynik = dict(d)
    for k, v in u.items():
        if k in d and isinstance(v, type(d[k])):
            wynik[k] = v
    wynik["zrodla"] = {z: bool(wynik["zrodla"].get(z, d["zrodla"][z])) for z in ZRODLA}
    wynik["frazy"] = [f for f in wynik["frazy"]
                      if isinstance(f, dict) and str(f.get("fraza", "")).strip()]
    if u.get("wersja", 1) < 2:
        # wersja 2 dodala RocketJobs i OLX - szukamy tam tego, co w pracuj.pl
        for f in wynik["frazy"]:
            z = list(f.get("zrodla") or [])
            if "pracuj.pl" in z:
                z += [x for x in ("RocketJobs", "OLX") if x not in z]
            f["zrodla"] = z
        wynik["wersja"] = 2
    return wynik


def zastosuj_ustawienia(u):
    """Ustawia listy, z ktorych korzysta pobieranie i filtry."""
    global KEYWORDS, KEYWORDS_LINKEDIN, KEYWORDS_USEME, KEYWORDS_ROCKET, KEYWORDS_OLX
    global WLACZONE_ZRODLA, MIASTO, MIASTO_NAZWA
    global OKOLICE, ZDALNE, STOP_TYTUL, STOP_JEZYK, POZIOMY_OK, LINKEDIN_LOKALIZACJE
    u = uzupelnij_ustawienia(u)
    frazy = [(f["fraza"].strip(), (f.get("kategoria") or "").strip() or "Inne", f.get("zrodla") or [])
             for f in u["frazy"]]
    KEYWORDS = [(f, k) for f, k, z in frazy if "pracuj.pl" in z]
    KEYWORDS_LINKEDIN = [(f, k) for f, k, z in frazy if "LinkedIn" in z]
    KEYWORDS_USEME = [f for f, k, z in frazy if "Useme" in z]
    KEYWORDS_ROCKET = [(f, k) for f, k, z in frazy if "RocketJobs" in z]
    KEYWORDS_OLX = [(f, k) for f, k, z in frazy if "OLX" in z]
    WLACZONE_ZRODLA = dict(u["zrodla"])
    MIASTO_NAZWA = u["miasto"].strip()                     # "" = cala Polska
    MIASTO = re.sub(r"\s+", "-", uprosc(MIASTO_NAZWA))           # adres pracuj.pl: .../warszawa;wp
    OKOLICE = [uprosc(x).strip() for x in [MIASTO_NAZWA] + u["okolice"] if x.strip()] \
        if MIASTO_NAZWA else []
    if MIASTO == "warszawa":
        OKOLICE.append("warsaw")
    ZDALNE = bool(u["zdalne"])
    STOP_TYTUL = [x for x in u["stop_tytul"] if x.strip()]
    STOP_JEZYK = [x for x in u["stop_jezyk"] if x.strip()]
    POZIOMY_OK = set(u["poziomy"])
    if not MIASTO_NAZWA:                    # bez miasta: cala Polska, zdalne sie w tym mieszcza
        LINKEDIN_LOKALIZACJE = [("Poland", "")]
    else:
        lok = ("Warszawa, Mazowieckie, Poland" if MIASTO == "warszawa"
               else "%s, Poland" % MIASTO_NAZWA)
        LINKEDIN_LOKALIZACJE = [(lok, "")]
        if ZDALNE:
            LINKEDIN_LOKALIZACJE.append(("Poland", "&f_WT=2"))   # f_WT=2 = praca zdalna
    return u


def grupa_poziomu(tekst):
    t = uprosc(tekst)
    for klucz, _, slowa in POZIOMY:
        if any(s in t for s in slowa):
            return klucz
    return None


def w_okolicy(tekst):
    if not OKOLICE:                 # miasto nieustawione - cala Polska sie liczy
        return True
    t = uprosc(tekst)
    return any(slowo in t for slowo in OKOLICE)


def pasuje_zasieg(o):
    return bool(o.get("warszawa")) or (ZDALNE and bool(o.get("zdalna")))


def pasuje_do_profilu(o):
    """Zwraca (True, "") albo (False, powod)."""
    tytul = uprosc(o.get("tytul", ""))
    for slowo in STOP_TYTUL:
        if uprosc(slowo) in tytul:
            return False, "tytuł: %s" % slowo
    for slowo in STOP_JEZYK:
        if uprosc(slowo) in tytul:
            return False, "język: %s" % slowo
    poziom = o.get("poziom") or ""
    if poziom:
        grupy = [grupa_poziomu(c) for c in poziom.split(",") if c.strip()]
        # nieznany poziom (grupa None) przepuszczamy - lepiej pokazac niz zgubic
        if grupy and not any(g is None or g in POZIOMY_OK for g in grupy):
            return False, "poziom: %s" % poziom
    return True, ""


zastosuj_ustawienia(None)   # domyslne, dopoki aplikacja nie wczyta zapisanych


# ---------------------------------------------------------------------------
# Zrodlo 1: pracuj.pl  (dane w JSON-ie __NEXT_DATA__)
# ---------------------------------------------------------------------------

def _pracuj_strona(fraza, kategoria, strona):
    if MIASTO:
        url = "https://www.pracuj.pl/praca/%s;kw/%s;wp" % (
            urllib.parse.quote(fraza), urllib.parse.quote(MIASTO))
    else:                                        # cala Polska
        url = "https://www.pracuj.pl/praca/%s;kw" % urllib.parse.quote(fraza)
    if strona > 1:
        url += "?pn=%d" % strona
    html = pobierz(url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        raise ValueError("brak danych na stronie (możliwa blokada)")
    dane = json.loads(m.group(1))
    zapytania = dane["props"]["pageProps"]["dehydratedState"]["queries"]
    grupy = []
    for q in zapytania:
        if q.get("queryKey") and q["queryKey"][0] == "jobOffers":
            grupy = (q.get("state", {}).get("data", {}) or {}).get("groupedOffers", [])
            break

    wynik = []
    for g in grupy:
        lokalizacje = [o.get("displayWorkplace", "") for o in g.get("offers", [])]
        lokalizacje = [x for x in lokalizacje if x]
        oferty_grupy = g.get("offers") or [{}]
        pierwsza = oferty_grupy[0]
        # oferta w kilku miastach: link do tej z Warszawy/okolic, nie do pierwszej z brzegu
        docelowa = next((o for o in oferty_grupy
                         if w_okolicy(o.get("displayWorkplace", "")) and o.get("offerAbsoluteUri")),
                        pierwsza)
        link = docelowa.get("offerAbsoluteUri") or pierwsza.get("offerAbsoluteUri", "")
        ident = str(pierwsza.get("partitionId") or g.get("groupId") or link)
        zdalna = bool(g.get("isRemoteWorkAllowed")) or any(
            "zdaln" in (w or "").lower() for w in g.get("workModes", []))
        miejsce = ", ".join(lokalizacje[:3])
        wynik.append({
            "id": "pracuj:" + ident,
            "zrodlo": "pracuj.pl",
            "tytul": g.get("jobTitle", "").strip(),
            "firma": (g.get("companyName") or "").strip(),
            "lokalizacja": miejsce,
            "warszawa": w_okolicy(miejsce) or any(
                o.get("isWholePoland") for o in g.get("offers", [])),
            "url": link,
            "opublikowano": (g.get("lastPublicated") or "")[:10],
            "wynagrodzenie": (g.get("salaryDisplayText") or "").strip(),
            "kategoria": kategoria,
            "zdalna": zdalna,
            "tryb": ", ".join(g.get("workModes", [])),
            "umowa": ", ".join(g.get("typesOfContract", [])),
            "poziom": ", ".join(g.get("positionLevels", [])),
            "opis": " ".join((g.get("jobDescription") or "").split())[:600],
            "termin": "",
        })
    return wynik


def zrodlo_pracuj(fraza, kategoria):
    wszystkie, widziane = [], set()
    for strona in range(1, PRACUJ_MAX_STRON + 1):
        oferty = _pracuj_strona(fraza, kategoria, strona)
        nowe = [o for o in oferty if o["id"] not in widziane]
        for o in nowe:
            widziane.add(o["id"])
        wszystkie.extend(nowe)
        if len(oferty) < 50 or not nowe:
            break
        time.sleep(PRZERWA)
    return wszystkie


# ---------------------------------------------------------------------------
# Zrodlo 2: LinkedIn (publiczny endpoint ofert, bez logowania)
# ---------------------------------------------------------------------------

LINKEDIN_LOKALIZACJE = [("Poland", "")]   # ustawiane przez zastosuj_ustawienia()


def zrodlo_linkedin(fraza, kategoria, lokalizacja, dodatek):
    url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"
           "search?keywords=%s&location=%s&start=0%s" % (
               urllib.parse.quote(fraza), urllib.parse.quote(lokalizacja), dodatek))
    html = pobierz(url, proby=1)     # przy odmowie nie ponawiamy - to tylko zlosci LinkedIn
    wynik = []
    for blok in bloki_po_klasie(html, "base-card"):
        link = atrybut_klasy(blok, "base-card__full-link", "href").split("?")[0]
        urn = blok["attrs"].get("data-entity-urn", "")
        ident = urn.split(":")[-1] if urn else link
        tytul = tekst_klasy(blok, "base-search-card__title")
        if not ident or not tytul:
            continue
        miejsce = tekst_klasy(blok, "job-search-card__location")
        wynik.append({
            "id": "linkedin:" + ident,
            "zrodlo": "LinkedIn",
            "tytul": tytul,
            "firma": tekst_klasy(blok, "base-search-card__subtitle"),
            "lokalizacja": miejsce,
            "warszawa": w_okolicy(miejsce),
            "url": link,
            "opublikowano": (atrybut_tagu(blok, "time", "datetime") or "")[:10],
            "wynagrodzenie": "",
            "kategoria": kategoria,
            "zdalna": bool(dodatek) or "zdaln" in miejsce.lower() or "remote" in miejsce.lower(),
            "tryb": "Zdalna" if dodatek else "",
            "umowa": "", "poziom": "", "opis": "", "termin": "",
        })
    return wynik


# ---------------------------------------------------------------------------
# Zrodlo 3: Useme (zlecenia freelance)
# ---------------------------------------------------------------------------

def zrodlo_useme(fraza):
    url = "https://useme.com/pl/jobs/?query=%s" % urllib.parse.quote(fraza)
    html = pobierz(url)
    wynik = []
    for blok in bloki_po_klasie(html, "job"):
        sciezka = atrybut_klasy(blok, "job__title-link", "href")
        tytul = tekst_klasy(blok, "job__title")
        if not sciezka or not tytul:
            continue
        m = re.search(r",(\d+)/?$", sciezka)
        ident = m.group(1) if m else sciezka
        opis = tekst_klasy(blok, "job__content")
        if opis.startswith(tytul):
            opis = opis[len(tytul):].strip()
        wynik.append({
            "id": "useme:" + ident,
            "zrodlo": "Useme",
            "tytul": tytul,
            "firma": "",
            "lokalizacja": "zdalnie",
            "warszawa": False,
            "url": "https://useme.com" + sciezka,
            "opublikowano": "",
            "wynagrodzenie": tekst_klasy(blok, "job__budget-value"),
            "kategoria": "Freelance",
            "zdalna": True,
            "tryb": "Zdalna",
            "umowa": "Zlecenie",
            "poziom": "",
            "opis": opis[:600],
            "termin": tekst_klasy(blok, "job__header-details--date"),
        })
    return wynik


# ---------------------------------------------------------------------------
# Zrodlo 4: WorkConnect (publiczne podglady kategorii)
# ---------------------------------------------------------------------------

def zrodlo_workconnect(sciezka):
    html = pobierz("https://www.workconnect.app" + sciezka)
    wynik, widziane = [], set()
    for blok in bloki_po_klasie(html, "button--surface"):
        link, tytul = link_po_prefiksie(blok, "/zlecenie/")
        if not link or not tytul or link in widziane:
            continue
        widziane.add(link)
        if tekst_klasy(blok, "line-through"):   # zlecenie zakonczone
            continue
        wynik.append({
            "id": "workconnect:" + link.rstrip("/").split("/")[-1],
            "zrodlo": "WorkConnect",
            "tytul": tytul,
            "firma": tekst_klasy(blok, "t-14-medium"),
            "lokalizacja": "zdalnie",
            "warszawa": False,
            "url": "https://www.workconnect.app" + link,
            "opublikowano": "",
            "wynagrodzenie": tekst_klasy(blok, "leading-4"),
            "kategoria": "Freelance",
            "zdalna": True,
            "tryb": "Zdalna",
            "umowa": "Zlecenie",
            "poziom": "",
            "opis": tekst_klasy(blok, "t-14-default")[:600],
            "termin": tekst_klasy(blok, "text-right"),
        })
    return wynik


# ---------------------------------------------------------------------------
# Zrodlo 5: RocketJobs.pl (ten sam operator co justjoin.it; publiczne API
# strony, bez logowania - te same dane, ktore widzi przegladarka)
# ---------------------------------------------------------------------------

ROCKET_API = "https://rocketjobs.pl/api/candidate-api/offers"
ROCKET_OFERTA = "https://rocketjobs.pl/oferta-pracy/%s"

ROCKET_POZIOMY = {"junior": "Junior", "mid": "Mid", "senior": "Senior",
                  "manager": "Manager", "c_level": "Dyrektor (C-level)"}
ROCKET_UMOWY = {"b2b": "B2B", "permanent": "Umowa o pracę", "mandate_contract": "Umowa zlecenie",
                "specific-task_contract": "Umowa o dzieło", "internship": "Staż"}
ROCKET_TRYB = {"office": "Stacjonarna", "hybrid": "Hybrydowa", "remote": "Zdalna"}
JEDNOSTKI = {"month": "mies.", "hour": "godz.", "day": "dzień", "year": "rok"}


def _liczba(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return ""
    return ("%d" % x if x == int(x) else "%.2f" % x).replace(",", " ")


def _rocket_wynagrodzenie(typy):
    kandydaci = [t for t in typy or [] if isinstance(t, dict) and t.get("from")
                 and (t.get("currencySource") or "original") == "original"]
    if not kandydaci:
        return ""
    t = kandydaci[0]
    kwota = _liczba(t.get("from"))
    if t.get("to") and t.get("to") != t.get("from"):
        kwota += "–" + _liczba(t.get("to"))
    tekst = "%s %s" % (kwota, (t.get("currency") or "pln").upper())
    if t.get("unit"):
        tekst += " / " + JEDNOSTKI.get(t["unit"], t["unit"])
    if t.get("type"):
        tekst += " (%s)" % ROCKET_UMOWY.get(t["type"], t["type"])
    return tekst


def zrodlo_rocketjobs(fraza, kategoria):
    params = [("keywords", fraza), ("keywordType", "any")]
    if MIASTO_NAZWA:
        params += [("city", MIASTO_NAZWA), ("cityRadius", "30")]
    params += [("itemsCount", "100"), ("from", "0"), ("sortBy", "publishedAt"),
               ("orderBy", "descending")]
    dane = json.loads(pobierz(ROCKET_API + "?" + urllib.parse.urlencode(params),
                              accept="application/json"))
    wynik = []
    for d in (dane.get("data") or []) if isinstance(dane, dict) else []:
        slug, ident = d.get("slug"), d.get("guid") or d.get("slug")
        if not slug or not d.get("title"):
            continue
        # "city" w API tylko ustawia kolejnosc, wiec lokalizacje sprawdzamy sami
        miasta = [d.get("city") or ""] + [m.get("city") or "" for m in d.get("multilocation") or []
                                          if isinstance(m, dict)]
        miasta = list(dict.fromkeys(m for m in miasta if m))
        tryb = d.get("workplaceType") or ""
        umowy = [ROCKET_UMOWY.get(t.get("type"), t.get("type") or "")
                 for t in d.get("employmentTypes") or [] if isinstance(t, dict)]
        umiejetnosci = [u.get("name") if isinstance(u, dict) else str(u)
                        for u in d.get("requiredSkills") or []]
        wynik.append({
            "id": "rocketjobs:" + str(ident),
            "zrodlo": "RocketJobs",
            "tytul": d["title"].strip(),
            "firma": (d.get("companyName") or "").strip(),
            "lokalizacja": ", ".join(miasta[:3]),
            "warszawa": any(w_okolicy(m) for m in miasta),
            "url": ROCKET_OFERTA % slug,
            "opublikowano": (d.get("publishedAt") or "")[:10],
            "wynagrodzenie": _rocket_wynagrodzenie(d.get("employmentTypes")),
            "kategoria": kategoria,
            "zdalna": tryb == "remote",
            "tryb": ROCKET_TRYB.get(tryb, tryb),
            "umowa": ", ".join(dict.fromkeys(u for u in umowy if u)),
            "poziom": ROCKET_POZIOMY.get(d.get("experienceLevel"), d.get("experienceLevel") or ""),
            "opis": ("Wymagane: " + ", ".join(u for u in umiejetnosci if u)) if umiejetnosci else "",
            "termin": "",
        })
    return wynik


# ---------------------------------------------------------------------------
# Zrodlo 6: OLX Praca (publiczne API strony, kategoria 4 = "Praca")
# ---------------------------------------------------------------------------

OLX_API = "https://www.olx.pl/api/v1/offers/"
OLX_STRON = 2           # po 40 ogloszen; OLX szuka w calej Polsce, miasto filtrujemy sami


def _olx_parametr(d, *klucze):
    for p in d.get("params") or []:
        if isinstance(p, dict) and p.get("key") in klucze:
            v = p.get("value")
            if isinstance(v, dict):
                if v.get("label"):
                    return str(v["label"])
                if v.get("from"):
                    tekst = _liczba(v["from"])
                    if v.get("to") and v.get("to") != v.get("from"):
                        tekst += "–" + _liczba(v["to"])
                    return tekst + " " + (v.get("currency") or "PLN")
            elif v:
                return str(v)
    return ""


def _olx_tekst(html):
    return " ".join(l["tekst"] for l in html_na_linie((html or "").replace("\n", "<br>")))


def zrodlo_olx(fraza, kategoria):
    wynik = []
    for strona in range(OLX_STRON):
        params = [("offset", str(strona * 40)), ("limit", "40"), ("category_id", "4"),
                  ("query", fraza), ("sort_by", "created_at:desc")]
        dane = json.loads(pobierz(OLX_API + "?" + urllib.parse.urlencode(params),
                                  accept="application/json"))
        oferty = (dane.get("data") or []) if isinstance(dane, dict) else []
        for d in oferty:
            if not isinstance(d, dict) or not d.get("url") or not d.get("title"):
                continue
            lok = d.get("location") or {}
            miasto = ((lok.get("city") or {}).get("name") or "") if isinstance(lok, dict) else ""
            dzielnica = ((lok.get("district") or {}).get("name") or "") if isinstance(lok, dict) else ""
            miejsce = ", ".join(x for x in (miasto, dzielnica) if x)
            opis = _olx_tekst(d.get("description"))
            wszystko = uprosc(" ".join([d["title"], opis] + [
                str((p.get("value") or {}).get("label") or "") for p in d.get("params") or []
                if isinstance(p, dict) and isinstance(p.get("value"), dict)]))
            uzytkownik = d.get("user") or {}
            wynik.append({
                "id": "olx:" + str(d.get("id") or d["url"]),
                "zrodlo": "OLX",
                "tytul": d["title"].strip(),
                "firma": (uzytkownik.get("company_name") or "").strip()
                         if isinstance(uzytkownik, dict) else "",
                "lokalizacja": miejsce,
                "warszawa": w_okolicy(miejsce),
                "url": d["url"],
                "opublikowano": (d.get("created_time") or d.get("last_refresh_time") or "")[:10],
                "wynagrodzenie": _olx_parametr(d, "salary"),
                "kategoria": kategoria,
                "zdalna": "zdaln" in wszystko or "remote" in wszystko,
                "tryb": _olx_parametr(d, "workplace", "remote"),
                "umowa": _olx_parametr(d, "agreement", "contract"),
                "poziom": "",
                "opis": opis[:600],
                "termin": "",
            })
        if len(oferty) < 40 or not ((dane.get("links") or {}).get("next")):
            break
        time.sleep(PRZERWA)
    return wynik


# ---------------------------------------------------------------------------
# Szczegoly oferty: obowiazki, wymagania, mile widziane, oferujemy
# (pobierane na zadanie, gdy rozwiniesz karte oferty)
# ---------------------------------------------------------------------------

SEKCJE = ["obowiazki", "wymagania", "mile_widziane", "oferujemy"]

# naglowki (bez polskich znakow, male litery) -> sekcja
# kolejnosc ma znaczenie: "Oferujemy ciekawe zadania" to oferta, nie obowiazki
SLOWA_SEKCJI = [
    ("mile_widziane", ["mile widzian", "nice to have", "nice-to-have", "dodatkowym atutem",
                       "atutem bedzie", "dodatkowe atuty", "bonus points", "preferred"]),
    ("oferujemy", ["oferujemy", "to oferujemy", "co oferujemy", "co zyskasz", "zapewniamy",
                   "w zamian", "benefit", "we offer", "what we offer", "perks", "why join",
                   "what's in it for you", "what you get"]),
    ("obowiazki", ["obowiazk", "zakres zadan", "zakres prac", "twoje zadania", "zadania",
                   "czym bedziesz sie zajmowac", "czym sie bedziesz zajmowac",
                   "co bedziesz robic", "twoja rola", "na czym polega", "zakres zlecenia",
                   "responsibilit", "your tasks", "tasks", "what you will do", "what you'll do",
                   "duties", "your role", "the role", "your job", "day-to-day", "day to day",
                   "what you'll be doing", "what you will be doing", "your impact"]),
    ("wymagania", ["wymagan", "oczekujemy", "oczekiwan", "kogo szukamy", "szukamy osoby",
                   "czego oczekujemy", "profil kandydat", "twoj profil", "kwalifikacj",
                   "umiejetnosci", "potrzebujemy", "requirement", "qualification",
                   "what we expect", "we expect", "we are looking for", "we're looking for",
                   "looking for", "must have", "must-have", "your profile", "about you",
                   "who you are", "you have", "what you need", "skills", "experience"]),
]

# pracuj.pl oznacza sekcje atrybutem data-test
SEKCJE_PRACUJ = {
    "section-responsibilities": "obowiazki",
    "section-requirements": "wymagania",
    "section-requirements-expected": "wymagania",
    "section-requirements-optional": "mile_widziane",
    "section-offered": "oferujemy",
    "section-benefits": "oferujemy",
}

BLOKOWE_TAGI = {"p", "div", "section", "article", "main", "ul", "ol", "li", "br", "h1", "h2",
                "h3", "h4", "h5", "h6", "tr", "td", "th", "table", "dd", "dt", "dl",
                "blockquote", "pre", "hr", "header", "footer", "aside", "figure"}
POMIJANE_TAGI = {"script", "style", "noscript", "svg", "template", "head", "nav", "footer",
                 "header", "form", "button", "select", "iframe", "aside", "title"}
KONTENERY = {"div", "section", "article", "main", "td"}
POGRUBIENIA = {"strong", "b"}
NAGLOWKI = {"h1", "h2", "h3", "h4", "h5", "h6"}


class Linie(HTMLParser):
    """Zamienia HTML na liste linii: {"tekst", "rodzaj", "sekcja", "sciezka", "linki"}.

    rodzaj: "naglowek" (h1-h6 albo cala linia pogrubiona), "punkt" (<li>)
    albo "tekst". sekcja: najblizszy data-test="section-..." (pracuj.pl).
    sciezka: numery kontenerow (div/section...), w ktorych lezy linia.
    klasa: jesli podana, zbiera tylko tekst z elementow o tej klasie.
    """

    def __init__(self, klasa=None):
        super().__init__(convert_charrefs=True)
        self.klasa = klasa
        self.stos = []
        self.linie = []
        self.licznik = 0
        self._nowa_linia()

    def _nowa_linia(self):
        self.czesci, self.w_linkach = [], 0
        self.tylko_pogrubione = True
        self.kontekst = None

    def _zamknij_linie(self):
        tekst = " ".join("".join(self.czesci).split())
        if tekst and self.kontekst:
            naglowek, punkt, sekcja, sciezka = self.kontekst
            if naglowek:
                rodzaj = "naglowek"
            elif punkt:
                rodzaj = "punkt"
            elif self.tylko_pogrubione and len(tekst) < 120:
                rodzaj = "naglowek"
            else:
                rodzaj = "tekst"
            self.linie.append({"tekst": tekst, "rodzaj": rodzaj, "sekcja": sekcja,
                               "sciezka": sciezka,
                               "linki": self.w_linkach / max(1, len(tekst))})
        self._nowa_linia()

    def handle_starttag(self, tag, attrs):
        if tag in BLOKOWE_TAGI:
            self._zamknij_linie()
        if tag in PUSTE_TAGI:
            return
        a = dict(attrs)
        wpis = {"tag": tag, "klasa": (a.get("class") or "").split(), "sekcja": None, "kontener": None}
        dt = a.get("data-test") or ""
        if dt.startswith("section-"):
            wpis["sekcja"] = dt
        if tag in KONTENERY:
            self.licznik += 1
            wpis["kontener"] = self.licznik
        self.stos.append(wpis)

    def handle_startendtag(self, tag, attrs):
        if tag in BLOKOWE_TAGI:
            self._zamknij_linie()

    def handle_endtag(self, tag):
        if tag in PUSTE_TAGI:
            return
        for i in range(len(self.stos) - 1, -1, -1):
            if self.stos[i]["tag"] == tag:
                if tag in BLOKOWE_TAGI or any(w["tag"] in BLOKOWE_TAGI for w in self.stos[i:]):
                    self._zamknij_linie()
                del self.stos[i:]
                return

    def handle_data(self, data):
        tagi = [w["tag"] for w in self.stos]
        if any(t in POMIJANE_TAGI for t in tagi):
            return
        if self.klasa and not any(self.klasa in w["klasa"] for w in self.stos):
            return
        if not data.strip():
            if self.czesci:
                self.czesci.append(" ")
            return
        if self.kontekst is None:
            sekcja = next((w["sekcja"] for w in reversed(self.stos) if w["sekcja"]), None)
            sciezka = tuple(w["kontener"] for w in self.stos if w["kontener"])
            self.kontekst = (any(t in NAGLOWKI for t in tagi), "li" in tagi, sekcja, sciezka)
        if not any(t in POGRUBIENIA or t in NAGLOWKI for t in tagi):
            self.tylko_pogrubione = False
        if "a" in tagi:
            self.w_linkach += len(data.strip())
        self.czesci.append(data)

    def close(self):
        super().close()
        self._zamknij_linie()


def html_na_linie(html, klasa=None):
    p = Linie(klasa)
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        pass
    return p.linie


PUNKTOR = re.compile(r"^\s*([•\-–—*·▪●◦✓✔➤►>]|\d{1,2}[.)])")


def sekcja_naglowka(tekst):
    if PUNKTOR.match(tekst):          # "• Experience with ..." to punkt, nie naglowek
        return None
    t = uprosc(tekst).strip(" :.-–—•*#")
    if not t or len(t) > 70:
        return None
    for sekcja, slowa in SLOWA_SEKCJI:
        if any(s in t for s in slowa):
            return sekcja
    return None


def _wyczysc(tekst):
    tekst = re.sub(r"^\s*([•\-–—*·▪●◦✓✔➤►>]|\d{1,2}[.)])\s*", "", tekst).strip()
    return tekst if len(tekst) <= 450 else tekst[:449].rstrip() + "…"


def _dopisz(lista, tekst):
    tekst = _wyczysc(tekst)
    if tekst and tekst not in lista and len(lista) < 30:
        lista.append(tekst)


def sekcje_z_linii(linie):
    """Dzieli linie wedlug naglowkow. Zwraca (sekcje, reszta_tekstu)."""
    wynik = {k: [] for k in SEKCJE}
    reszta = []
    biezaca = None
    for l in linie:
        tekst, rodzaj = l["tekst"], l["rodzaj"]
        k = None
        if rodzaj == "naglowek" or (rodzaj == "tekst" and len(tekst) <= 70 and
                                    (tekst.rstrip().endswith(":") or len(tekst) <= 30)):
            k = sekcja_naglowka(tekst)
            if k:
                biezaca = k
                continue
            if rodzaj == "naglowek":      # inny naglowek ("O firmie") - konczy sekcje
                biezaca = "inne"
                continue
        # "Wymagania: znajomosc Google Ads, ..." w jednej linii
        if ":" in tekst[:45] and rodzaj != "punkt":
            glowa, _, ogon = tekst.partition(":")
            k = sekcja_naglowka(glowa)
            if k:
                biezaca = k
                for kawalek in re.split(r"\s*[;•]\s*", ogon):
                    _dopisz(wynik[k], kawalek)
                continue
        if biezaca in wynik:
            _dopisz(wynik[biezaca], tekst)
        else:
            reszta.append(tekst)
    return wynik, reszta


def _gotowe(sekcje, reszta, metoda):
    """Sekcje + ogolny opis (tekst spoza sekcji: wstep, opis zlecenia).

    Gdy sa sekcje, opis jest krotki (wstep); gdy ich nie ma - dluzszy."""
    dane = {k: v for k, v in sekcje.items() if v}
    limit = 700 if dane else 2500
    opis, dlugosc = [], 0
    for t in reszta:
        t = _wyczysc(t)
        if len(t) < 3 or t in opis:
            continue
        if opis and dlugosc + len(t) > limit:
            break
        opis.append(t)
        dlugosc += len(t)
        if len(opis) >= 25:
            break
    if opis:
        dane["opis"] = opis
    if dane:
        dane["metoda"] = metoda
    return dane


def _json_ld(html):
    """Oferty pracy w formacie schema.org (JobPosting) - ma je wiele serwisow."""
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html,
                         re.DOTALL | re.IGNORECASE):
        try:
            dane = json.loads(m.group(1).strip())
        except ValueError:
            continue
        kolejka = [dane]
        while kolejka:
            d = kolejka.pop()
            if isinstance(d, list):
                kolejka.extend(d)
            elif isinstance(d, dict):
                typ = d.get("@type")
                if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                    return d
                kolejka.extend(v for v in d.values() if isinstance(v, (list, dict)))
    return None


def _z_json_ld(oferta_ld):
    sekcje = {k: [] for k in SEKCJE}
    for pole, k in (("responsibilities", "obowiazki"), ("qualifications", "wymagania"),
                    ("experienceRequirements", "wymagania"), ("skills", "wymagania"),
                    ("jobBenefits", "oferujemy")):
        wartosc = oferta_ld.get(pole)
        if isinstance(wartosc, dict):
            wartosc = wartosc.get("description") or ""
        for v in (wartosc if isinstance(wartosc, list) else [wartosc]):
            if isinstance(v, str) and v.strip():
                for l in html_na_linie(v):
                    if l["rodzaj"] != "naglowek":
                        _dopisz(sekcje[k], l["tekst"])
    opis = oferta_ld.get("description") or ""
    z_opisu, reszta = sekcje_z_linii(html_na_linie(opis if "<" in opis else
                                                   opis.replace("\n", "<br>")))
    for k in SEKCJE:
        if not sekcje[k]:
            sekcje[k] = z_opisu[k]
    return _gotowe(sekcje, reszta, "json-ld")


def _glowny_tekst(linie):
    """Linie z kontenera, w ktorym jest najwiecej tresci (nie menu, nie linki)."""
    tresc = [l for l in linie if l["linki"] < 0.5 and len(l["tekst"]) > 1]
    if not tresc:
        return []
    suma = {}
    for l in tresc:
        for k in l["sciezka"]:
            suma[k] = suma.get(k, 0) + len(l["tekst"])
    if not suma:
        return tresc
    najwiecej = max(suma.values())
    # najglebszy kontener, ktory ma co najmniej 60% tresci najwiekszego
    glebokosc = {}
    for l in tresc:
        for i, k in enumerate(l["sciezka"]):
            glebokosc[k] = max(glebokosc.get(k, 0), i)
    kandydaci = [k for k, v in suma.items() if v >= 0.6 * najwiecej]
    wybrany = max(kandydaci, key=lambda k: (glebokosc[k], suma[k]))
    return [l for l in tresc if wybrany in l["sciezka"]]


def _z_linii(linie, metoda):
    sekcje, reszta = sekcje_z_linii(linie)
    return _gotowe(sekcje, reszta, metoda)


def szczegoly_z_html(html, zrodlo=""):
    """Wyciaga sekcje z calej strony oferty. Zwraca slownik (moze byc pusty)."""
    linie = html_na_linie(html)

    # 1. pracuj.pl: sekcje oznaczone data-test
    sekcje = {k: [] for k in SEKCJE}
    o_projekcie = []
    for l in linie:
        if l["rodzaj"] == "naglowek":
            continue
        k = SEKCJE_PRACUJ.get(l["sekcja"] or "")
        if k:
            _dopisz(sekcje[k], l["tekst"])
        elif l["sekcja"] == "section-about-project":
            o_projekcie.append(l["tekst"])
    if any(sekcje.values()):
        return _gotowe(sekcje, o_projekcie, "pracuj-sekcje")

    # 2. LinkedIn: opis w bloku o znanej klasie
    for klasa in ("show-more-less-html__markup", "description__text"):
        if klasa in html:
            dane = _z_linii(html_na_linie(html, klasa), "linkedin")
            if dane:
                return dane

    # 3. schema.org JobPosting
    oferta_ld = _json_ld(html)
    if oferta_ld:
        dane = _z_json_ld(oferta_ld)
        if dane:
            return dane

    # 4. najwiekszy blok tekstu na stronie
    return _z_linii(_glowny_tekst(linie), "strona")


def pobierz_szczegoly(oferta):
    """Pobiera strone oferty i zwraca jej sekcje.

    Wynik: {"obowiazki": [...], "wymagania": [...], "mile_widziane": [...],
            "oferujemy": [...]} albo {"opis": [...]}, gdy strona nie ma sekcji.
    Rzuca wyjatek, gdy strony nie da sie pobrac albo nic na niej nie ma.
    """
    url = oferta.get("url") or ""
    if not url:
        raise ValueError("oferta nie ma linku")
    zrodlo = oferta.get("zrodlo") or ""
    ident = (oferta.get("id") or "").split(":", 1)[-1]
    html = None
    # OLX i RocketJobs: pelny opis jest w API strony (to samo, co widzi przegladarka)
    try:
        if zrodlo == "OLX" and ident.isdigit():
            d = json.loads(pobierz(OLX_API + ident + "/", accept="application/json"))
            opis = ((d.get("data") or {}) if isinstance(d, dict) else {}).get("description") or ""
            if opis:
                html = "<div>%s</div>" % opis.replace("\n", "<br>")
        elif zrodlo == "RocketJobs":
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            d = json.loads(pobierz(ROCKET_API + "/" + urllib.parse.quote(slug),
                                   accept="application/json"))
            d = d.get("data", d) if isinstance(d, dict) else {}
            if isinstance(d, dict) and d.get("body"):
                html = "<div>%s</div>" % d["body"]
    except Exception:
        html = None          # nie wyszlo - sprobujemy zwykla strone oferty
    if html is not None:
        dane = szczegoly_z_html(html, zrodlo)
        if dane:
            return dane
        html = None
    if zrodlo == "LinkedIn" and ident.isdigit():
        # publiczny podglad oferty (bez logowania) - sam opis, bez reszty strony
        try:
            html = pobierz("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + ident)
        except Exception:
            html = None
    if html is None:
        html = pobierz(url)
    dane = szczegoly_z_html(html, zrodlo)
    if not dane:
        raise ValueError("na stronie oferty nie znalazłem opisu")
    return dane


# ---------------------------------------------------------------------------
# Pelny przebieg
# ---------------------------------------------------------------------------

def zbierz_wszystko(log=print, przerwij=None, pomin=None):
    """Pobiera oferty ze wszystkich zrodel i przepuszcza przez filtry.

    pomin: {zrodlo: powod} - zrodla, ktorych tym razem nie pytamy
           (np. LinkedIn, gdy byl pytany niedawno albo odmowil dostepu).

    Zwraca slownik:
      oferty          - {id: oferta} po filtrach
      udane_zrodla    - zrodla, w ktorych KAZDE zapytanie sie udalo
                        (tylko dla nich wolno oznaczac znikniete oferty)
      pytane_zrodla   - zrodla, o ktore faktycznie pytalismy
      odmowy          - zrodla, ktore odmowily dostepu (blad 429/999)
      bledy           - lista opisow bledow
      odrzucone       - lista (tytul, powod) odsianych przez filtr profilu
    """
    pomin = dict(pomin or {})
    zebrane, bledy = {}, []
    porazki = {z: 0 for z in WLACZONE_ZRODLA}
    niepelne = set()     # zrodla, w ktorych nie wszystkie zapytania poszly
    odmowy = set()

    def stop():
        return przerwij is not None and przerwij.is_set()

    def dodaj(oferty):
        for o in oferty:
            zebrane.setdefault(o["id"], o)

    for zrodlo, powod in pomin.items():
        if WLACZONE_ZRODLA.get(zrodlo):
            log("%s: pomijam - %s" % (zrodlo, powod))

    def wlaczone(zrodlo):
        return WLACZONE_ZRODLA.get(zrodlo) and zrodlo not in pomin

    zadania = []
    if wlaczone("pracuj.pl"):
        for fraza, kat in KEYWORDS:
            zadania.append(("pracuj.pl", "'%s'" % fraza,
                            lambda f=fraza, k=kat: zrodlo_pracuj(f, k), PRZERWA))
    if wlaczone("RocketJobs"):
        for fraza, kat in KEYWORDS_ROCKET:
            zadania.append(("RocketJobs", "'%s'" % fraza,
                            lambda f=fraza, k=kat: zrodlo_rocketjobs(f, k), PRZERWA))
    if wlaczone("OLX"):
        for fraza, kat in KEYWORDS_OLX:
            zadania.append(("OLX", "'%s'" % fraza,
                            lambda f=fraza, k=kat: zrodlo_olx(f, k), PRZERWA))
    if wlaczone("LinkedIn"):
        linkedin = []
        for fraza, kat in KEYWORDS_LINKEDIN:
            for lok, dod in LINKEDIN_LOKALIZACJE:
                linkedin.append(("LinkedIn", "'%s' / %s" % (fraza, lok.split(",")[0]),
                                 lambda f=fraza, k=kat, l=lok, d=dod: zrodlo_linkedin(f, k, l, d),
                                 PRZERWA_LINKEDIN))
        if len(linkedin) > LINKEDIN_MAX_ZAPYTAN:
            log("LinkedIn: %d zapytań to za dużo - biorę pierwsze %d (w Ustawieniach "
                "zostaw LinkedIn przy mniejszej liczbie fraz)" % (len(linkedin), LINKEDIN_MAX_ZAPYTAN))
            linkedin = linkedin[:LINKEDIN_MAX_ZAPYTAN]
            niepelne.add("LinkedIn")
        zadania += linkedin
    if wlaczone("Useme"):
        for fraza in KEYWORDS_USEME:
            zadania.append(("Useme", "'%s'" % fraza,
                            lambda f=fraza: zrodlo_useme(f), PRZERWA))
    if wlaczone("WorkConnect"):
        for sciezka in WORKCONNECT_STRONY:
            zadania.append(("WorkConnect", sciezka,
                            lambda s=sciezka: zrodlo_workconnect(s), PRZERWA))

    pytane = set()
    for nr, (zrodlo, opis, funkcja, przerwa) in enumerate(zadania, 1):
        if stop():
            log("Przerwano.")
            break
        if zrodlo in odmowy:          # odmowil raz - nie pytamy go dalej
            niepelne.add(zrodlo)
            continue
        pytane.add(zrodlo)
        try:
            oferty = funkcja()
            dodaj(oferty)
            log("[%d/%d] %s %s -> %d" % (nr, len(zadania), zrodlo, opis, len(oferty)))
        except urllib.error.HTTPError as e:
            porazki[zrodlo] += 1
            bledy.append("%s %s: %s" % (zrodlo, opis, e))
            if e.code in KODY_ODMOWY:
                odmowy.add(zrodlo)
                log("[%d/%d] %s odmówił dostępu (błąd %d) - nie pytam go więcej w tym "
                    "odświeżeniu" % (nr, len(zadania), zrodlo, e.code))
            else:
                log("[%d/%d] %s %s -> BŁĄD: %s" % (nr, len(zadania), zrodlo, opis, e))
        except Exception as e:
            porazki[zrodlo] += 1
            bledy.append("%s %s: %s" % (zrodlo, opis, e))
            log("[%d/%d] %s %s -> BŁĄD: %s" % (nr, len(zadania), zrodlo, opis, e))
        time.sleep(przerwa)

    przed = len(zebrane)
    zebrane = {k: v for k, v in zebrane.items() if pasuje_zasieg(v)}
    if przed != len(zebrane):
        log("Poza zasięgiem (nie %s i nie zdalne): %d" % (MIASTO_NAZWA or "Twoje miasto",
                                                          przed - len(zebrane)))

    pasujace, odrzucone = {}, []
    for k, v in zebrane.items():
        ok, powod = pasuje_do_profilu(v)
        if ok:
            pasujace[k] = v
        else:
            odrzucone.append((v["tytul"], powod))
    if odrzucone:
        log("Nie na Twój poziom / nie Twoja działka: %d" % len(odrzucone))

    # oferty "znikaja" tylko ze zrodel, ktore odpytalismy w calosci i bez bledu
    udane = [z for z, n in porazki.items()
             if n == 0 and z in pytane and z not in niepelne and z not in pomin]
    if stop():
        udane = []   # przerwany przebieg nie moze niczego "wygaszac"
    return {"oferty": pasujace, "udane_zrodla": udane,
            "pytane_zrodla": sorted(pytane), "odmowy": sorted(odmowy),
            "bledy": bledy, "odrzucone": odrzucone}


if __name__ == "__main__":
    # uruchomienie tego pliku otwiera okno aplikacji
    import runpy, os
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "Radar Karier.pyw"), run_name="__main__")
