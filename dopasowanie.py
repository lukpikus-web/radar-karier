# -*- coding: utf-8 -*-
"""
Radar Karier - dopasowanie ofert do CV.

Wszystko liczy sie na Twoim komputerze - CV nie jest nigdzie wysylane.
Nie wymaga instalowania bibliotek (jesli jest zainstalowane pypdf,
zostanie uzyte do czytania PDF-ow; bez niego dziala wbudowany czytnik).

Jak liczony jest wynik (0-100%):
  - pokrycie wymagan: ile umiejetnosci z oferty (wymagania, obowiazki,
    tytul, opis) masz w CV - 60% wyniku,
  - podobienstwo tekstu CV i oferty (TF-IDF) - 40% wyniku,
  - premia za umiejetnosci "mile widziane", ktore masz.
"""

import base64
import math
import re
import zipfile
import zlib
from xml.etree import ElementTree

OGONKI = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def uprosc(tekst):
    return (tekst or "").translate(OGONKI).lower()


# ---------------------------------------------------------------------------
# Slownik umiejetnosci: (nazwa, [wzorce]) - wzorce to wyrazenia regularne
# na tekscie bez polskich znakow, malymi literami.
# Wlasne umiejetnosci dopisane w aplikacji dzialaja obok tego slownika.
# ---------------------------------------------------------------------------

UMIEJETNOSCI = [
    # reklama i marketing online
    ("Google Ads", [r"google ads", r"adwords"]),
    ("Meta Ads", [r"meta ads", r"facebook ads", r"\bfb ads", r"ads manager", r"meta business"]),
    ("TikTok Ads", [r"tiktok ads"]),
    ("LinkedIn Ads", [r"linkedin ads"]),
    ("Allegro Ads", [r"allegro ads"]),
    ("Microsoft Ads", [r"microsoft ads", r"bing ads"]),
    ("Google Analytics", [r"google analytics", r"\bga ?4\b"]),
    ("Google Tag Manager", [r"tag manager", r"\bgtm\b"]),
    ("Looker Studio", [r"looker studio", r"data studio"]),
    ("Merchant Center", [r"merchant center"]),
    ("Search Console", [r"search console"]),
    ("SEO", [r"\bseo\b", r"pozycjonowani"]),
    ("SEM / PPC", [r"\bsem\b", r"\bppc\b", r"pay[ -]per[ -]click", r"kampani\w* platn",
                   r"paid (media|search|social)"]),
    ("Performance marketing", [r"performance marketing", r"marketing\w* efektywnosciow"]),
    ("Social media", [r"social media", r"media spolecznosciow", r"social mediow"]),
    ("Content marketing", [r"content marketing", r"tworzeni\w* tresci", r"content creat"]),
    ("Copywriting", [r"copywrit"]),
    ("E-mail marketing", [r"e-?mail marketing", r"newsletter", r"mailchimp", r"getresponse",
                          r"freshmail"]),
    ("Marketing automation", [r"marketing automation", r"automatyzacj\w* marketing"]),
    ("E-commerce", [r"e-?commerce", r"sklep\w* internetow"]),
    ("Shopify", [r"shopify"]),
    ("WooCommerce", [r"woocommerce"]),
    ("PrestaShop", [r"prestashop"]),
    ("WordPress", [r"wordpress"]),
    ("Testy A/B", [r"\ba/?b test", r"testy a/?b", r"testow\w* a/?b"]),
    ("Analiza danych", [r"analiz\w* danych", r"data analy", r"analityk\w* danych"]),
    ("Raportowanie", [r"raport", r"reporting"]),
    ("Budżetowanie", [r"budzet", r"\bbudget"]),
    ("Canva", [r"\bcanva\b"]),
    ("Photoshop", [r"photoshop"]),
    ("Figma", [r"\bfigma\b"]),
    ("HTML / CSS", [r"\bhtml", r"\bcss\b"]),
    ("SQL", [r"\bsql\b"]),
    ("Python", [r"\bpython\b"]),
    ("Power BI", [r"power ?bi\b"]),
    ("Tableau", [r"tableau"]),
    ("Excel", [r"\bexcel"]),
    ("MS Office", [r"ms office", r"microsoft office", r"pakiet\w* office", r"office 365",
                   r"\bpakiet\w* ms\b"]),
    ("PowerPoint", [r"power ?point"]),
    ("Google Workspace", [r"google (workspace|sheets|docs|arkusze)", r"arkusz\w* google"]),
    # sprzedaz i obsluga klienta
    ("Sprzedaż B2B", [r"\bb2b\b"]),
    ("Sprzedaż B2C", [r"\bb2c\b"]),
    ("Salesforce", [r"salesforce"]),
    ("HubSpot", [r"hubspot"]),
    ("Pipedrive", [r"pipedrive"]),
    ("CRM", [r"\bcrm\b"]),
    ("SAP", [r"\bsap\b"]),
    ("Negocjacje", [r"negocjac", r"negotiat"]),
    ("Pozyskiwanie klientów", [r"pozyskiw\w* (nowych )?klient", r"lead gen", r"generowani\w* lead",
                               r"new business", r"business development", r"prospect",
                               r"akwizycj"]),
    ("Key account", [r"key account", r"kluczow\w* klient", r"klient\w* kluczow", r"\bkam\b"]),
    ("Account management", [r"account manag", r"opiek\w* nad klient", r"opiekun\w* klient"]),
    ("Obsługa klienta", [r"obslug\w* klient", r"customer (service|support|success)"]),
    ("Sprzedaż telefoniczna", [r"cold call", r"telesprzedaz", r"sprzedaz\w* telefoniczn",
                               r"telemarketing"]),
    ("Prezentacje", [r"prezentacj", r"presentation"]),
    ("Zarządzanie projektami", [r"zarzadzani\w* projekt", r"project manag"]),
    ("Jira", [r"\bjira\b"]),
    ("Asana", [r"\basana\b"]),
    ("Trello", [r"\btrello\b"]),
    # jezyki
    ("Angielski", [r"angielsk", r"\benglish\b"]),
    ("Niemiecki", [r"niemieck", r"\bgerman\b"]),
    ("Francuski", [r"francusk", r"\bfrench\b"]),
    ("Hiszpański", [r"hiszpansk", r"\bspanish\b"]),
    ("Włoski", [r"\bwlosk", r"\bitalian\b"]),
    ("Rosyjski", [r"rosyjsk", r"\brussian\b"]),
    ("Ukraiński", [r"ukrainsk", r"\bukrainian\b"]),
    # inne
    ("Prawo jazdy kat. B", [r"prawo jazdy", r"prawa jazdy", r"driving licen[cs]e"]),
    ("Certyfikat Google", [r"certyfika\w* google", r"google (ads )?certif", r"skillshop"]),
]

_SLOWNIK = [(n, [re.compile(w) for w in wz]) for n, wz in UMIEJETNOSCI]

# kto zna jedno, zna tez drugie: Google Ads to kampanie SEM/PPC, Salesforce to CRM...
WYNIKA = {
    "Google Ads": ["SEM / PPC", "Performance marketing"],
    "Microsoft Ads": ["SEM / PPC"],
    "Allegro Ads": ["SEM / PPC"],
    "Meta Ads": ["Performance marketing"],
    "TikTok Ads": ["Performance marketing"],
    "LinkedIn Ads": ["Performance marketing"],
    "Salesforce": ["CRM"],
    "HubSpot": ["CRM"],
    "Pipedrive": ["CRM"],
    "Excel": ["MS Office"],
    "PowerPoint": ["MS Office"],
    "Key account": ["Account management"],
    "Looker Studio": ["Raportowanie"],
    "Power BI": ["Raportowanie", "Analiza danych"],
    "Tableau": ["Raportowanie", "Analiza danych"],
    "Certyfikat Google": ["Google Ads"],
}


def _wzorzec_wlasny(nazwa):
    t = re.escape(uprosc(nazwa).strip())
    return re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % t)


def kanoniczna(nazwa):
    """"facebook ads" -> "Meta Ads"; nieznana nazwa zostaje bez zmian."""
    t = uprosc(nazwa).strip()
    for n, wzorce in _SLOWNIK:
        if uprosc(n) == t or any(w.search(t) for w in wzorce):
            return n
    return " ".join(nazwa.split())


def wykryj_umiejetnosci(tekst, wlasne=()):
    """Nazwy umiejetnosci znalezionych w tekscie (slownik + wlasne)."""
    t = uprosc(tekst)
    wynik = [n for n, wzorce in _SLOWNIK if any(w.search(t) for w in wzorce)]
    for w in wlasne:
        n = kanoniczna(w)
        if n not in wynik and _wzorzec_wlasny(w).search(t):
            wynik.append(n)
    return wynik


# ---------------------------------------------------------------------------
# Podobienstwo tekstu (TF-IDF). Polskie odmiany sprowadzamy do wspolnego
# poczatku slowa: "sprzedazy", "sprzedaz", "sprzedawca" -> "sprzed".
# ---------------------------------------------------------------------------

STOP = set("""
a aby ale albo ani az bardzo bedzie bez beda byc byl byla bylo cie ci co czy dla do dzieki
gdy gdzie go i ich ile im inne jak jako jakie jego jej jest jestem jesli jezeli juz kazdy
kiedy kto ktora ktore ktory ktorych lub ma mamy maja mi mozesz moze na nad nam nas nasz
nasza nasze naszego naszej nie niz no o od oraz po pod pan pani poprzez przez przy roku
sie sa sobie sposob ta tak takze tam te tego tej ten to tu ty tylko w we wie wiele wraz
wsrod z za ze zakres oferty oferta osoba osob praca pracy prace firma firmy naszym
the and for with you your our are will have has from that this can who all any not
in on at to of as be by an or is it we us per
""".split())


def slowa(tekst):
    wynik = []
    for s in re.findall(r"[a-z0-9+#]+", uprosc(tekst)):
        if len(s) < 3 and s not in ("ai", "ux", "ui", "bi", "hr", "it", "pr", "b2b", "b2c"):
            continue
        if s in STOP or s.isdigit():
            continue
        wynik.append(s[:6])
    return wynik


def _wektor(tokeny, idf, domyslne_idf):
    tf = {}
    for t in tokeny:
        tf[t] = tf.get(t, 0) + 1
    w = {t: (1 + math.log(n)) * idf.get(t, domyslne_idf) for t, n in tf.items()}
    norma = math.sqrt(sum(v * v for v in w.values())) or 1.0
    return {t: v / norma for t, v in w.items()}


def _kosinus(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


class Profil:
    """CV + umiejetnosci, gotowe do oceniania ofert."""

    def __init__(self, tekst_cv, umiejetnosci, teksty_ofert=()):
        self.umiejetnosci = []
        for u in umiejetnosci:
            n = kanoniczna(u)
            if n and n not in self.umiejetnosci:
                self.umiejetnosci.append(n)
        self.moje = set(self.umiejetnosci)
        for u in list(self.moje):
            self.moje.update(WYNIKA.get(u, []))
        # wlasne (spoza slownika) tez szukamy w ofertach
        znane = {n for n, _ in _SLOWNIK}
        self.wlasne = [u for u in self.umiejetnosci if u not in znane]
        # IDF z ofert w bazie
        dokumenty = [set(slowa(t)) for t in teksty_ofert]
        n = len(dokumenty) + 1
        df = {}
        for d in dokumenty:
            for t in d:
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        self.domyslne_idf = math.log(n + 1) + 1
        tekst = (tekst_cv or "") + (" " + " ".join(self.umiejetnosci)) * 2
        self.cv = _wektor(slowa(tekst), self.idf, self.domyslne_idf)
        self.pusty = not self.cv

    def _pokrycie_tekstu(self, tokeny):
        """Jaka czesc waznych slow oferty (wazonych rzadkoscia) jest w CV. 0-1."""
        wagi = {}
        for t in tokeny:
            wagi[t] = wagi.get(t, 0) + 1
        suma = trafione = 0.0
        for t, n in wagi.items():
            w = (1 + math.log(n)) * self.idf.get(t, self.domyslne_idf)
            suma += w
            if t in self.cv:
                trafione += w
        if not suma:
            return 0.0
        # tytul+krotki opis: pokrycie liczy sie w calosci; dlugie oferty maja
        # duzo "szumu" (o firmie, benefity), wiec skala jest lagodniejsza
        return min(1.0, (trafione / suma) / 0.6)

    def ocen(self, oferta, szczegoly=None):
        """{"wynik": 0-100, "pasuje": [...], "brakuje": [...], "wstepny": bool}"""
        sz = szczegoly or {}
        glowne = " ".join([oferta.get("tytul") or "", oferta.get("opis") or ""] +
                          sz.get("wymagania", []) + sz.get("obowiazki", []) + sz.get("opis", []))
        mile = " ".join(sz.get("mile_widziane", []))
        potrzebne = wykryj_umiejetnosci(glowne, self.wlasne)
        mile_u = [u for u in wykryj_umiejetnosci(mile, self.wlasne) if u not in potrzebne]
        pasuje = [u for u in potrzebne if u in self.moje]
        brakuje = [u for u in potrzebne if u not in self.moje]
        pasuje_mile = [u for u in mile_u if u in self.moje]

        tekst = " ".join([(oferta.get("tytul") or "") + " "] * 3 + [oferta.get("opis") or ""] +
                         sz.get("wymagania", []) * 2 + sz.get("obowiazki", []) +
                         sz.get("mile_widziane", []) + sz.get("opis", []))
        podobne = self._pokrycie_tekstu(slowa(tekst))

        if potrzebne:
            pokrycie = len(pasuje) / len(potrzebne)
            wynik = 0.6 * pokrycie + 0.4 * podobne
        else:
            wynik = 0.8 * podobne          # nic znanego w ofercie - sam tekst
        if mile_u:
            wynik += 0.1 * len(pasuje_mile) / len(mile_u)
        wynik = int(round(100 * min(1.0, wynik)))
        # sam tytul (np. LinkedIn bez opisu) to za malo, zeby mowic o 100%:
        # wynik szacunkowy, dokladny po pobraniu opisu oferty
        wstepny = not szczegoly and len(oferta.get("opis") or "") < 150
        if wstepny:
            wynik = min(wynik, 75)
        return {"wynik": wynik, "pasuje": pasuje + pasuje_mile, "brakuje": brakuje,
                "wstepny": wstepny}


# ---------------------------------------------------------------------------
# Frazy wyszukiwania z CV: stanowiska + umiejetnosci, po ktorych warto szukac
# (zeby po dodaniu CV nie trzeba bylo recznie wpisywac slow kluczowych)
# ---------------------------------------------------------------------------

# Slowa, od ktorych zaczyna sie nazwa stanowiska (bez polskich znakow).
STANOWISKA = [
    # polskie
    "specjalist", "asystent", "konsultant", "doradc", "handlowiec", "przedstawiciel",
    "kierownik", "koordynator", "menedzer", "menadzer", "analityk", "ksiegow", "sprzedawc",
    "kasjer", "magazynier", "kierowc", "programist", "tester", "grafik", "projektant",
    "copywriter", "marketer", "recepcjonist", "sekretar", "telemarketer", "rekruter",
    "inzynier", "technik", "operator", "logistyk", "spedytor", "nauczyciel", "opiekun",
    "pielegniar", "fizjoterapeut", "kucharz", "kelner", "barist", "fryzjer", "elektryk",
    "mechanik", "architekt", "prawnik", "ratownik", "stazyst", "praktykant", "dyrektor",
    "referent", "kontroler", "planista", "zaopatrzeniow", "pracownik biurow",
    # angielskie
    "specialist", "assistant", "consultant", "representative", "manager", "coordinator",
    "analyst", "engineer", "developer", "designer", "executive", "officer", "associate",
    "advisor", "adviser", "agent", "administrator", "accountant", "recruiter", "cashier",
    "marketer", "intern", "trainee", "strategist", "planner", "buyer", "account manager",
]
_STANOWISKO = re.compile(r"(?<![a-z])(%s)[a-z]*" % "|".join(re.escape(s) for s in STANOWISKA))
_POZIOM = re.compile(r"^(mlodsz[ya]|starsz[ya]|junior|senior|mid|regular|glown[ya])\s+")
_OGOLNE = {"manager", "specjalista", "specjalistka", "specialist", "pracownik", "konsultant",
           "asystent", "asystentka", "koordynator", "kierownik", "analityk", "consultant",
           "assistant", "agent", "officer", "associate", "executive", "dyrektor", "operator",
           "technik", "referent", "intern", "trainee", "stazysta", "praktykant"}

# umiejetnosc ze slownika -> fraza do wyszukiwarki (tylko te, po ktorych
# wyszukiwanie ma sens; "Excel" czy "Angielski" dalyby tysiace przypadkowych ofert)
SZUKAJ_PO = {
    "Google Ads": "google ads", "Meta Ads": "meta ads", "TikTok Ads": "tiktok ads",
    "LinkedIn Ads": "linkedin ads", "Allegro Ads": "allegro ads", "Microsoft Ads": "microsoft ads",
    "SEO": "seo", "SEM / PPC": "ppc", "Performance marketing": "performance marketing",
    "Social media": "social media", "Content marketing": "content marketing",
    "Copywriting": "copywriter", "E-mail marketing": "email marketing",
    "Marketing automation": "marketing automation", "E-commerce": "e-commerce",
    "Google Analytics": "google analytics", "Salesforce": "salesforce", "HubSpot": "hubspot",
    "SAP": "sap", "SQL": "sql", "Python": "python", "Power BI": "power bi", "Tableau": "tableau",
    "Shopify": "shopify", "WordPress": "wordpress", "Figma": "figma",
    "Key account": "key account manager", "Account management": "account manager",
    "Pozyskiwanie klientów": "business development", "Sprzedaż telefoniczna": "telesprzedaż",
    "Analiza danych": "analityk danych", "Zarządzanie projektami": "project manager",
    "Sprzedaż B2B": "sprzedaż b2b", "Obsługa klienta": "obsługa klienta",
}

MAX_STANOWISK = 4
MAX_UMIEJETNOSCI = 6


_ANGIELSKIE = set(STANOWISKA[STANOWISKA.index("specialist"):])


def _czysc(slowa):
    """Bez dat i liczb ("2019-2024") i bez poziomu ("Mlodszy", "Senior") na poczatku."""
    wynik = [s for s in slowa if not re.fullmatch(r"[\d.\-–/]+|obecnie|present|now", uprosc(s))]
    while wynik and _POZIOM.match(uprosc(wynik[0]) + " "):
        wynik = wynik[1:]
    return wynik


def _stanowisko_z_linii(linia):
    """"Firma X — Specjalista Google Ads (2024–2025)" -> "specjalista google ads"."""
    kawalki = re.split(r"\s[—–-]\s|[|,;:()\[\]@•·]|\s(?:w|at|dla|for|in|u)\s", linia)
    for k in kawalki:
        slowa = _czysc(k.split())
        if not slowa:
            continue
        u = [uprosc(s) for s in slowa]
        # ktore slowo to stanowisko?
        idx, rola = None, None
        for i in range(len(u)):
            m = _STANOWISKO.match(" ".join(u[i:]))
            if m:
                idx, rola = i, m.group(1)
                break
        if idx is None:
            continue
        if rola in _ANGIELSKIE:
            if idx > 3:                   # "Digital Marketing Specialist" - max 3 slowa przed
                continue
            start = 0
        else:
            if idx > 0:                   # po polsku stanowisko jest na poczatku
                continue                  # ("wspolpraca z kierownikiem" to nie stanowisko)
            start = 0
        wynik = []
        for s in slowa[start:]:
            if uprosc(s) in ("i", "oraz", "and", "&", "/"):
                break
            wynik.append(s)
            if len(wynik) >= 5:
                break
        fraza = " ".join(wynik).lower().strip(" .-")
        if not fraza or (len(wynik) == 1 and uprosc(fraza) in _OGOLNE):
            continue
        return fraza
    return None


def frazy_z_cv(tekst, umiejetnosci=()):
    """Frazy do wyszukiwania ofert na podstawie CV.

    Zwraca liste (fraza, "stanowisko" | "umiejetnosc"): najpierw nazwy
    stanowisk (naglowek CV i doswiadczenie), potem umiejetnosci, po
    ktorych warto szukac. Bez powtorzen."""
    linie = [l.strip() for l in (tekst or "").splitlines() if l.strip()]
    kandydaci = []
    for nr, l in enumerate(linie):
        slowa = l.split()
        if not slowa or len(slowa) > 16 or re.match(r"^[-•*▪●–—>✓]", l):
            continue                              # punkty z obowiazkami to nie stanowiska
        punkty = 0
        if nr < 4:
            punkty += 2                           # naglowek CV (kim jestem)
        if re.search(r"(19|20)\d\d", l):
            punkty += 2                           # linia z datami = doswiadczenie
        if len(slowa) > 10:
            punkty -= 1
        fraza = _stanowisko_z_linii(l)
        if fraza:
            kandydaci.append((-punkty, nr, fraza))
    wynik, widziane = [], set()
    for _, _, fraza in sorted(kandydaci):
        k = uprosc(fraza)
        if k not in widziane and not any(k in w or w in k for w in widziane if len(w.split()) > 1):
            widziane.add(k)
            wynik.append((fraza, "stanowisko"))
        if len(wynik) >= MAX_STANOWISK:
            break
    # umiejetnosci: czesciej wspominane w CV wyzej
    t = uprosc(tekst)
    lista = [kanoniczna(u) for u in umiejetnosci] or wykryj_umiejetnosci(tekst)
    oceny = []
    for n, (nazwa, wzorce) in enumerate(_SLOWNIK):
        if nazwa in lista and nazwa in SZUKAJ_PO:
            ile = sum(len(w.findall(t)) for w in wzorce)
            oceny.append((-ile, n, SZUKAJ_PO[nazwa]))
    for _, _, fraza in sorted(oceny)[:MAX_UMIEJETNOSCI]:
        if uprosc(fraza) not in widziane:
            widziane.add(uprosc(fraza))
            wynik.append((fraza, "umiejetnosc"))
    return wynik


# ---------------------------------------------------------------------------
# Czytanie pliku CV: TXT, DOCX, ODT, PDF
# ---------------------------------------------------------------------------

class BladCV(Exception):
    pass


def wczytaj_cv(sciezka):
    s = sciezka.lower()
    if s.endswith(".pdf"):
        tekst = tekst_pdf(sciezka)
    elif s.endswith(".docx"):
        tekst = _tekst_xml_zip(sciezka, "word/document.xml", "w")
    elif s.endswith(".odt"):
        tekst = _tekst_xml_zip(sciezka, "content.xml", "text")
    elif s.endswith((".txt", ".md")):
        with open(sciezka, "rb") as f:
            dane = f.read()
        for kod in ("utf-8-sig", "cp1250", "latin-1"):
            try:
                tekst = dane.decode(kod)
                break
            except UnicodeDecodeError:
                continue
    elif s.endswith(".doc"):
        raise BladCV("stary format .doc nie jest obsługiwany - zapisz CV jako PDF albo DOCX "
                     "(Plik → Zapisz jako) albo wklej tekst")
    else:
        raise BladCV("nieznany format pliku - wybierz PDF, DOCX, ODT albo TXT")
    tekst = _porzadek(tekst)
    litery = sum(c.isalpha() for c in tekst)
    if litery < 80:
        raise BladCV("nie udało się odczytać tekstu z tego pliku (to może być skan albo obrazek). "
                     "Otwórz CV, zaznacz wszystko (Ctrl+A), skopiuj (Ctrl+C) i wklej tutaj")
    return tekst


def _porzadek(tekst):
    linie = [" ".join(l.split()) for l in (tekst or "").replace("\r", "\n").split("\n")]
    wynik, puste = [], 0
    for l in linie:
        if l:
            wynik.append(l)
            puste = 0
        elif puste == 0 and wynik:
            wynik.append("")
            puste = 1
    return "\n".join(wynik).strip()


def _tekst_xml_zip(sciezka, plik, prefiks):
    try:
        with zipfile.ZipFile(sciezka) as z:
            xml = z.read(plik)
    except (zipfile.BadZipFile, KeyError) as e:
        raise BladCV("plik jest uszkodzony albo to nie jest %s (%s)" % (plik.split("/")[0], e))
    korzen = ElementTree.fromstring(xml)
    linie, biezaca = [], []

    def nazwa(el):
        return el.tag.rsplit("}", 1)[-1]

    def idz(el):
        n = nazwa(el)
        if n in ("t",) or (prefiks == "text" and n == "span"):
            pass
        if n == "t" and el.text:                       # DOCX: <w:t>
            biezaca.append(el.text)
        elif n in ("tab",):
            biezaca.append("\t")
        elif n in ("br", "cr", "line-break"):
            biezaca.append("\n")
        elif n == "s" and prefiks == "text":           # ODT: <text:s/> = spacje
            biezaca.append(" ")
        if prefiks == "text" and n in ("p", "h", "span", "a") and el.text:
            biezaca.append(el.text)
        for dziecko in el:
            idz(dziecko)
            if prefiks == "text" and dziecko.tail:
                biezaca.append(dziecko.tail)
        if n in ("p", "h"):                            # koniec akapitu
            linie.append("".join(biezaca))
            biezaca.clear()

    idz(korzen)
    if biezaca:
        linie.append("".join(biezaca))
    return "\n".join(linie)


# --- PDF ---------------------------------------------------------------------

def tekst_pdf(sciezka):
    try:                               # jesli ktos ma pypdf - lepsza jakosc
        import pypdf
        czytnik = pypdf.PdfReader(sciezka)
        tekst = "\n".join((s.extract_text() or "") for s in czytnik.pages)
        if sum(c.isalpha() for c in tekst) >= 80:
            return tekst
    except Exception:
        pass
    with open(sciezka, "rb") as f:
        dane = f.read()
    if not dane.startswith(b"%PDF"):
        raise BladCV("to nie jest plik PDF")
    if b"/Encrypt" in dane:
        raise BladCV("PDF jest zabezpieczony - wklej tekst CV ręcznie")
    return CzytnikPDF(dane).tekst()


_BIALE = b" \t\r\n\x0c\x00"
_OGRANICZNIKI = b"()<>[]{}/%"


def _tokeny(d):
    """Tokeny skladni PDF: ("<<",), (">>",), ("[",), ("]",), ("name", s),
    ("str", bajty), ("num", liczba), ("op", s)."""
    i, n = 0, len(d)
    while i < n:
        c = d[i]
        if c in _BIALE:
            i += 1
        elif c == 0x25:                                   # % komentarz
            while i < n and d[i] not in b"\r\n":
                i += 1
        elif d.startswith(b"<<", i):
            yield ("<<",)
            i += 2
        elif d.startswith(b">>", i):
            yield (">>",)
            i += 2
        elif c == 0x3C:                                   # <hex>
            j = d.find(b">", i)
            j = n if j < 0 else j
            hx = re.sub(rb"[^0-9A-Fa-f]", b"", d[i + 1:j])
            if len(hx) % 2:
                hx += b"0"
            yield ("str", bytes.fromhex(hx.decode()))
            i = j + 1
        elif c == 0x28:                                   # (tekst)
            wynik, glebokosc, i = bytearray(), 1, i + 1
            while i < n:
                c = d[i]
                if c == 0x5C:                             # \
                    i += 1
                    if i >= n:
                        break
                    c = d[i]
                    zn = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}.get(c)
                    if zn is not None:
                        wynik.append(zn)
                    elif 0x30 <= c <= 0x37:
                        osm = bytes([c])
                        while len(osm) < 3 and i + 1 < n and 0x30 <= d[i + 1] <= 0x37:
                            i += 1
                            osm += bytes([d[i]])
                        wynik.append(int(osm, 8) & 0xFF)
                    elif c in b"\r\n":
                        if c == 0x0D and i + 1 < n and d[i + 1] == 0x0A:
                            i += 1
                    else:
                        wynik.append(c)
                elif c == 0x28:
                    glebokosc += 1
                    wynik.append(c)
                elif c == 0x29:
                    glebokosc -= 1
                    if glebokosc == 0:
                        break
                    wynik.append(c)
                else:
                    wynik.append(c)
                i += 1
            yield ("str", bytes(wynik))
            i += 1
        elif c in b"[]":
            yield (chr(c),)
            i += 1
        elif c == 0x2F:                                   # /Nazwa
            j = i + 1
            while j < n and d[j] not in _BIALE and d[j] not in _OGRANICZNIKI:
                j += 1
            yield ("name", d[i + 1:j].decode("latin-1"))
            i = j
        elif c in b"{}":
            i += 1
        else:
            j = i
            while j < n and d[j] not in _BIALE and d[j] not in _OGRANICZNIKI:
                j += 1
            s = d[i:j].decode("latin-1")
            i = max(j, i + 1)
            try:
                yield ("num", float(s))
            except ValueError:
                yield ("op", s)


class _Ref:
    __slots__ = ("num",)

    def __init__(self, num):
        self.num = num


def _parsuj(tokeny):
    """Pierwsza wartosc z tokenow (slownik, tablica, liczba, referencja...)."""
    stos = [[]]
    for t in tokeny:
        k = t[0]
        if k in ("<<", "["):
            stos.append([k])
        elif k in (">>", "]"):
            if len(stos) < 2:
                break
            elementy = stos.pop()
            if elementy[0] == "<<":
                pary = elementy[1:]
                wartosc = {}
                for i in range(0, len(pary) - 1, 2):
                    if isinstance(pary[i], tuple) and pary[i][0] == "name":
                        wartosc[pary[i][1]] = pary[i + 1]
            else:
                wartosc = elementy[1:]
            stos[-1].append(wartosc)
        elif k == "op" and t[1] == "R" and len(stos[-1]) >= 2:
            stos[-1].pop()
            num = stos[-1].pop()
            stos[-1].append(_Ref(int(num[1]) if isinstance(num, tuple) else int(num)))
        elif k == "num":
            stos[-1].append(t)
        else:
            stos[-1].append(t)
        if len(stos) == 1 and stos[0] and not (k == "num"):
            if not (len(stos[0]) >= 1 and isinstance(stos[0][-1], tuple) and stos[0][-1][0] == "num"):
                return stos[0][-1]
    return stos[0][-1] if stos[0] else None


def _liczba(v, domyslna=0.0):
    if isinstance(v, tuple) and v[0] == "num":
        return v[1]
    return domyslna


class CzytnikPDF:
    """Prosty czytnik tekstu z PDF (bez bibliotek). Obsluguje strumienie
    FlateDecode, strumienie obiektow i czcionki z mapa ToUnicode
    (tak zapisuja Word, LibreOffice, Canva, przegladarki)."""

    def __init__(self, dane):
        self.obiekty = {}
        for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)\bendobj", dane, re.S):
            self.obiekty[int(m.group(1))] = m.group(2)
        for tresc in list(self.obiekty.values()):
            slownik, strumien = self._strumien(tresc)
            if isinstance(slownik, dict) and self._nazwa(slownik.get("Type")) == "ObjStm" and strumien:
                try:
                    n, pierwszy = int(_liczba(slownik.get("N"))), int(_liczba(slownik.get("First")))
                    nag = strumien[:pierwszy].split()
                    pary = [(int(nag[i]), int(nag[i + 1])) for i in range(0, 2 * n, 2)]
                    for i, (num, off) in enumerate(pary):
                        koniec = pary[i + 1][1] if i + 1 < len(pary) else len(strumien) - pierwszy
                        self.obiekty.setdefault(num, strumien[pierwszy + off:pierwszy + koniec])
                except (ValueError, IndexError):
                    pass
        self._cache = {}
        self._cmapy = {}

    # --- obiekty ---
    def _strumien(self, tresc):
        k = tresc.find(b"stream")
        if k < 0:
            return _parsuj(_tokeny(tresc)), None
        slownik = _parsuj(_tokeny(tresc[:k]))
        start = k + 6
        if tresc[start:start + 2] == b"\r\n":
            start += 2
        elif tresc[start:start + 1] in (b"\n", b"\r"):
            start += 1
        koniec = tresc.rfind(b"endstream")
        surowe = tresc[start:koniec if koniec > 0 else len(tresc)].rstrip(b"\r\n")
        filtry = slownik.get("Filter") if isinstance(slownik, dict) else None
        filtry = filtry if isinstance(filtry, list) else [filtry] if filtry else []
        dane = surowe
        for f in filtry:                   # filtry stosuje sie po kolei
            dane = self._odfiltruj(dane, self._nazwa(f))
            if dane is None:
                return slownik, None       # obrazy itp. - nie potrzebujemy
        return slownik, dane

    @staticmethod
    def _odfiltruj(dane, filtr):
        try:
            if filtr in ("FlateDecode", "Fl"):
                try:
                    return zlib.decompress(dane)
                except zlib.error:
                    return zlib.decompressobj().decompress(dane)
            if filtr in ("ASCII85Decode", "A85"):
                d = dane.strip()
                if d.startswith(b"<~"):
                    d = d[2:]
                if d.endswith(b"~>"):
                    d = d[:-2]
                return base64.a85decode(d)
            if filtr in ("ASCIIHexDecode", "AHx"):
                d = re.sub(rb"[^0-9A-Fa-f]", b"", dane.split(b">")[0])
                return bytes.fromhex((d + b"0" * (len(d) % 2)).decode())
        except (zlib.error, ValueError):
            return None
        return None

    def obiekt(self, v):
        """Rozwiazuje referencje; zwraca (slownik_lub_wartosc, strumien_lub_None)."""
        if isinstance(v, _Ref):
            if v.num not in self._cache:
                tresc = self.obiekty.get(v.num)
                self._cache[v.num] = self._strumien(tresc) if tresc is not None else (None, None)
            return self._cache[v.num]
        return v, None

    def wart(self, v):
        return self.obiekt(v)[0]

    @staticmethod
    def _nazwa(v):
        return v[1] if isinstance(v, tuple) and v[0] == "name" else None

    # --- strony ---
    def strony(self):
        wynik = []
        for num in sorted(self.obiekty):
            sl, _ = self.obiekt(_Ref(num))
            if isinstance(sl, dict) and self._nazwa(sl.get("Type")) == "Page":
                wynik.append(sl)
        katalog = [self.wart(_Ref(n)) for n in self.obiekty]
        korzenie = [k for k in katalog if isinstance(k, dict) and self._nazwa(k.get("Type")) == "Catalog"]
        if korzenie:                         # kolejnosc stron wg drzewa, jesli sie da
            uporzadkowane = []
            self._zbierz(korzenie[0].get("Pages"), uporzadkowane, 0)
            if uporzadkowane:
                return uporzadkowane
        return wynik

    def _zbierz(self, v, wynik, glebokosc):
        sl = self.wart(v)
        if not isinstance(sl, dict) or glebokosc > 30:
            return
        if self._nazwa(sl.get("Type")) == "Page":
            wynik.append(sl)
            return
        for dziecko in self.wart(sl.get("Kids")) or []:
            self._zbierz(dziecko, wynik, glebokosc + 1)

    def _zasoby(self, strona):
        sl, glebokosc = strona, 0
        while isinstance(sl, dict) and glebokosc < 30:
            if "Resources" in sl:
                return self.wart(sl["Resources"]) or {}
            sl = self.wart(sl.get("Parent"))
            glebokosc += 1
        return {}

    # --- czcionki ---
    def _cmapa(self, czcionka):
        """(slownik kod->tekst, szerokosc kodu w bajtach) dla czcionki."""
        klucz = id(czcionka)
        if klucz in self._cmapy:
            return self._cmapy[klucz]
        mapa, szer = {}, 1
        if isinstance(czcionka, dict):
            if self._nazwa(czcionka.get("Subtype")) == "Type0":
                szer = 2
            _, strumien = self.obiekt(czcionka.get("ToUnicode"))
            if strumien:
                mapa, szer_cmap = self._parsuj_cmap(strumien)
                szer = szer_cmap or szer
        self._cmapy[klucz] = (mapa, szer)
        return mapa, szer

    @staticmethod
    def _parsuj_cmap(dane):
        mapa, szer = {}, None
        def utf16(b):
            try:
                return b.decode("utf-16-be")
            except UnicodeDecodeError:
                return ""
        for blok in re.findall(rb"begincodespacerange(.*?)endcodespacerange", dane, re.S):
            hx = re.findall(rb"<([0-9A-Fa-f]+)>", blok)
            if hx:
                szer = len(hx[0]) // 2
        for blok in re.findall(rb"beginbfchar(.*?)endbfchar", dane, re.S):
            hx = re.findall(rb"<([0-9A-Fa-f]*)>", blok)
            for i in range(0, len(hx) - 1, 2):
                mapa[int(hx[i] or b"0", 16)] = utf16(bytes.fromhex(hx[i + 1].decode()))
        for blok in re.findall(rb"beginbfrange(.*?)endbfrange", dane, re.S):
            for m in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", blok):
                od, do = int(m.group(1), 16), int(m.group(2), 16)
                cel = m.group(3)
                if cel.startswith(b"["):
                    for i, h in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", cel)):
                        mapa[od + i] = utf16(bytes.fromhex(h.decode()))
                else:
                    baza = bytes.fromhex(cel[1:-1].decode())
                    if len(baza) < 2:
                        continue
                    for kod in range(od, min(do, od + 5000) + 1):
                        ostatni = int.from_bytes(baza[-2:], "big") + (kod - od)
                        mapa[kod] = utf16(baza[:-2] + (ostatni & 0xFFFF).to_bytes(2, "big"))
        return mapa, szer

    def _szerokosci(self, czcionka):
        """(slownik kod->szerokosc w 1/1000 em, domyslna)."""
        klucz = ("w", id(czcionka))
        if klucz in self._cmapy:
            return self._cmapy[klucz]
        sz, domyslna = {}, 500.0
        if isinstance(czcionka, dict):
            if self._nazwa(czcionka.get("Subtype")) == "Type0":
                potomki = self.wart(czcionka.get("DescendantFonts")) or []
                cid = self.wart(potomki[0]) if potomki else None
                if isinstance(cid, dict):
                    domyslna = _liczba(self.wart(cid.get("DW")), 1000.0)
                    w = self.wart(cid.get("W")) or []
                    i = 0
                    while i < len(w):
                        pierwszy = int(_liczba(w[i]))
                        nast = self.wart(w[i + 1]) if i + 1 < len(w) else None
                        if isinstance(nast, list):
                            for k, v in enumerate(nast):
                                sz[pierwszy + k] = _liczba(v, domyslna)
                            i += 2
                        elif i + 2 < len(w):
                            for k in range(pierwszy, min(int(_liczba(nast)), pierwszy + 70000) + 1):
                                sz[k] = _liczba(w[i + 2], domyslna)
                            i += 3
                        else:
                            break
            else:
                pierwszy = int(_liczba(self.wart(czcionka.get("FirstChar")), 0))
                for k, v in enumerate(self.wart(czcionka.get("Widths")) or []):
                    sz[pierwszy + k] = _liczba(self.wart(v), 500.0)
                opis = self.wart(czcionka.get("FontDescriptor"))
                if isinstance(opis, dict) and "MissingWidth" in opis:
                    domyslna = _liczba(opis.get("MissingWidth"), 500.0) or 500.0
        self._cmapy[klucz] = (sz, domyslna)
        return sz, domyslna

    def _dekoduj(self, bajty, czcionka):
        """(tekst, suma szerokosci znakow w 1/1000 em)."""
        mapa, szer = self._cmapa(czcionka)
        szerokosci, domyslna = self._szerokosci(czcionka)
        if not mapa:
            szer = 1
        wynik, suma = [], 0.0
        for i in range(0, len(bajty) - szer + 1, szer):
            kod = int.from_bytes(bajty[i:i + szer], "big")
            zn = mapa.get(kod) if mapa else None
            if zn is None and szer == 1:
                zn = bytes([kod]).decode("cp1252", errors="ignore")
            wynik.append(zn or "")
            suma += szerokosci.get(kod, domyslna)
        return "".join(wynik), suma

    # --- tekst ---
    def tekst(self):
        strony = []
        for strona in self.strony():
            self._linie, self._biezaca = [], []
            self._ost = None
            zasoby = self._zasoby(strona)
            zawartosc = self.wart(strona.get("Contents")) if not isinstance(strona.get("Contents"), _Ref) \
                else strona.get("Contents")
            czesci = zawartosc if isinstance(zawartosc, list) else [zawartosc]
            dane = b"\n".join((self.obiekt(c)[1] or b"") for c in czesci if c is not None)
            self._wykonaj(dane, zasoby, 0)
            self._nowa_linia()
            strony.append("\n".join(self._linie))
        return "\n\n".join(strony)

    def _nowa_linia(self):
        if self._biezaca:
            self._linie.append("".join(self._biezaca))
            self._biezaca = []

    def _dopisz(self, tekst, x, y, koniec, rozmiar):
        if not tekst:
            return
        if self._ost is not None:
            oy, okoniec, orozmiar = self._ost
            prog = max(1.0, 0.5 * max(rozmiar, orozmiar))
            if abs(y - oy) > prog:
                self._nowa_linia()
            elif abs(x - okoniec) > 0.2 * rozmiar and self._biezaca and \
                    not self._biezaca[-1].endswith(" ") and not tekst.startswith(" "):
                self._biezaca.append(" ")      # odstep miedzy slowami
        self._biezaca.append(tekst)
        self._ost = (y, koniec, rozmiar)

    def _wykonaj(self, dane, zasoby, glebokosc):
        if glebokosc > 5:
            return
        czcionki = self.wart(zasoby.get("Font")) if isinstance(zasoby, dict) else {}
        czcionki = czcionki if isinstance(czcionki, dict) else {}
        xobj = self.wart(zasoby.get("XObject")) if isinstance(zasoby, dict) else {}
        xobj = xobj if isinstance(xobj, dict) else {}
        czcionka, rozmiar, prowadzenie = None, 10.0, 0.0
        tm = [1.0, 0, 0, 1.0, 0.0, 0.0]
        tlm = list(tm)
        stos = []

        def skala():
            return max(abs(tm[0]), abs(tm[3]), abs(tm[1]), abs(tm[2]), 1e-6)

        def td(tx, ty):
            tlm[4] += tx * tlm[0] + ty * tlm[2]
            tlm[5] += tx * tlm[1] + ty * tlm[3]
            tm[:] = tlm

        def przesun(tx):                 # przesuniecie w poziomie w jednostkach tekstu
            tm[4] += tx * tm[0]
            tm[5] += tx * tm[1]

        def pokaz(b):
            tekst, szer = self._dekoduj(b, czcionka)
            x, y = tm[4], tm[5]
            przesun(szer / 1000.0 * rozmiar)
            self._dopisz(tekst, x, y, tm[4], rozmiar * skala())

        for t in self._z_tablicami(_tokeny(dane)):
            if t[0] != "op":
                stos.append(t)
                continue
            op = t[1]
            try:
                if op == "BT":
                    tm[:] = [1.0, 0, 0, 1.0, 0.0, 0.0]
                    tlm[:] = tm
                elif op == "Tf" and len(stos) >= 2:
                    czcionka = self.wart(czcionki.get(stos[-2][1])) if stos[-2][0] == "name" else None
                    rozmiar = _liczba(stos[-1], 10.0) or 10.0
                elif op == "TL" and stos:
                    prowadzenie = _liczba(stos[-1])
                elif op == "Td" and len(stos) >= 2:
                    td(_liczba(stos[-2]), _liczba(stos[-1]))
                elif op == "TD" and len(stos) >= 2:
                    prowadzenie = -_liczba(stos[-1])
                    td(_liczba(stos[-2]), _liczba(stos[-1]))
                elif op == "Tm" and len(stos) >= 6:
                    tm[:] = [_liczba(v) for v in stos[-6:]]
                    tlm[:] = tm
                elif op == "T*":
                    td(0, -prowadzenie)
                elif op == "Tj" and stos and stos[-1][0] == "str":
                    pokaz(stos[-1][1])
                elif op in ("'", '"') and stos and stos[-1][0] == "str":
                    td(0, -prowadzenie)
                    pokaz(stos[-1][1])
                elif op == "TJ" and stos and stos[-1][0] == "arr":
                    for el in stos[-1][1]:
                        if el[0] == "str":
                            pokaz(el[1])
                        elif el[0] == "num":       # odstep/kerning w 1/1000 em
                            przesun(-el[1] / 1000.0 * rozmiar)
                elif op == "Do" and stos and stos[-1][0] == "name":
                    forma, strumien = self.obiekt(xobj.get(stos[-1][1]))
                    if isinstance(forma, dict) and self._nazwa(forma.get("Subtype")) == "Form" and strumien:
                        self._wykonaj(strumien, self.wart(forma.get("Resources")) or zasoby, glebokosc + 1)
            except (TypeError, ValueError, IndexError):
                pass
            stos = []

    @staticmethod
    def _z_tablicami(tokeny):
        """Skleja [ ... ] w jeden token ("arr", [...]) - potrzebne dla TJ."""
        tablica = None
        for t in tokeny:
            if t[0] == "[":
                tablica = []
            elif t[0] == "]":
                if tablica is not None:
                    yield ("arr", tablica)
                tablica = None
            elif tablica is not None:
                tablica.append(t)
            else:
                yield t
