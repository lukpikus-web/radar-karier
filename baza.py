# -*- coding: utf-8 -*-
"""
Radar Karier - baza danych (SQLite, plik radar.db).

Najwazniejsza zasada: Twoje statusy i notatki siedza w OSOBNEJ tabeli
("statusy") niz oferty pobierane z internetu ("oferty"). Odswiezanie ofert
nigdy nie dotyka tabeli statusow, wiec nie moze ich skasowac.
Kazda zmiana statusu trafia tez do tabeli "historia" - do wgladu i ratunku.
"""

import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone

POLA_OFERTY = ["id", "zrodlo", "tytul", "firma", "lokalizacja", "url",
               "opublikowano", "wynagrodzenie", "kategoria", "zdalna",
               "warszawa", "tryb", "umowa", "poziom", "opis", "termin"]

SCHEMAT = """
CREATE TABLE IF NOT EXISTS oferty (
    id TEXT PRIMARY KEY,
    zrodlo TEXT, tytul TEXT, firma TEXT, lokalizacja TEXT, url TEXT,
    opublikowano TEXT, wynagrodzenie TEXT, kategoria TEXT,
    zdalna INTEGER DEFAULT 0, warszawa INTEGER DEFAULT 0,
    tryb TEXT, umowa TEXT, poziom TEXT, opis TEXT, termin TEXT,
    pierwszy_raz TEXT, ostatnio_widziana TEXT,
    aktywna INTEGER DEFAULT 1,
    nieobecnosci INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS statusy (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'nowa',
    notatka TEXT NOT NULL DEFAULT '',
    zmieniono TEXT
);
CREATE TABLE IF NOT EXISTS historia (
    nr INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT, co TEXT, wartosc TEXT, kiedy TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    klucz TEXT PRIMARY KEY,
    wartosc TEXT
);
CREATE TABLE IF NOT EXISTS szczegoly (
    id TEXT PRIMARY KEY,
    dane TEXT NOT NULL,
    pobrano TEXT
);
CREATE TABLE IF NOT EXISTS duplikaty (
    id TEXT PRIMARY KEY,        -- oferta, ktorej nie pokazujemy osobno...
    glowna TEXT NOT NULL,       -- ...bo to ta sama oferta co ta
    zrodlo TEXT, url TEXT,
    widziano TEXT               -- kiedy ostatnio byla w serwisie (link "tez na")
);
"""

# oferte, ktora juz oznaczyles, pokazujemy znowu jako nowa dopiero po tylu dniach
# (to zwykle nowa rekrutacja na to samo stanowisko)
DNI_DO_PONOWNEGO_POKAZANIA = 60

# ile odswiezen z rzedu oferta musi byc nieobecna, zeby uznac ja za wygasla
PROG_WYGASNIECIA = 2


def teraz():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def otworz(sciezka):
    # check_same_thread=False: w nowej wersji baze obsluguje serwer z kilku watkow
    # (pod wspolna blokada - patrz serwer.py)
    conn = sqlite3.connect(sciezka, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(SCHEMAT)
    kolumny = {r[1] for r in conn.execute("PRAGMA table_info(oferty)")}
    if "poprzednia" not in kolumny:     # starsza baza: info "wczesniej oznaczona jako..."
        conn.execute("ALTER TABLE oferty ADD COLUMN poprzednia TEXT")
    conn.commit()
    return conn


def meta_get(conn, klucz, domyslna=None):
    r = conn.execute("SELECT wartosc FROM meta WHERE klucz=?", (klucz,)).fetchone()
    return r["wartosc"] if r else domyslna


def meta_set(conn, klucz, wartosc):
    conn.execute("INSERT INTO meta(klucz, wartosc) VALUES(?, ?) "
                 "ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc",
                 (klucz, wartosc))
    conn.commit()


def liczba_ofert(conn):
    return conn.execute("SELECT COUNT(*) FROM oferty").fetchone()[0]


# ---------------------------------------------------------------------------
# Statusy i notatki - kazda zmiana zapisywana od razu (commit)
# ---------------------------------------------------------------------------

def ustaw_status(conn, ident, status):
    kiedy = teraz()
    with conn:   # transakcja: albo wszystko, albo nic
        conn.execute(
            "INSERT INTO statusy(id, status, zmieniono) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, zmieniono=excluded.zmieniono",
            (ident, status, kiedy))
        conn.execute("INSERT INTO historia(id, co, wartosc, kiedy) VALUES(?, 'status', ?, ?)",
                     (ident, status, kiedy))
    return kiedy


def ustaw_notatke(conn, ident, tekst):
    kiedy = teraz()
    with conn:
        conn.execute(
            "INSERT INTO statusy(id, notatka, zmieniono) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET notatka=excluded.notatka, zmieniono=excluded.zmieniono",
            (ident, tekst, kiedy))
        conn.execute("INSERT INTO historia(id, co, wartosc, kiedy) VALUES(?, 'notatka', ?, ?)",
                     (ident, tekst, kiedy))
    return kiedy


# ---------------------------------------------------------------------------
# Szczegoly ofert (obowiazki, wymagania...) - pobierane na zadanie, trzymane
# osobno, zeby nie pobierac tej samej oferty drugi raz
# ---------------------------------------------------------------------------

def wszystkie_szczegoly(conn):
    wynik = {}
    for w in conn.execute("SELECT id, dane, pobrano FROM szczegoly"):
        try:
            d = json.loads(w["dane"])
        except ValueError:
            continue
        d["pobrano"] = w["pobrano"]
        wynik[w["id"]] = d
    return wynik


def zapisz_szczegoly(conn, ident, dane):
    kiedy = teraz()
    with conn:
        conn.execute(
            "INSERT INTO szczegoly(id, dane, pobrano) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET dane=excluded.dane, pobrano=excluded.pobrano",
            (ident, json.dumps(dane, ensure_ascii=False), kiedy))
    return kiedy


def wszystkie(conn):
    """Oferty razem z Twoimi statusami, jako lista slownikow."""
    wiersze = conn.execute("""
        SELECT o.*, COALESCE(s.status, 'nowa') AS status,
               COALESCE(s.notatka, '') AS notatka, s.zmieniono AS zmieniono
        FROM oferty o LEFT JOIN statusy s ON s.id = o.id
    """).fetchall()
    # ta sama oferta w innych serwisach (widziana w ostatnich 2 tygodniach)
    granica = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tez = {}
    for d in conn.execute("SELECT glowna, zrodlo, url FROM duplikaty WHERE widziano >= ? "
                          "ORDER BY widziano DESC", (granica,)):
        tez.setdefault(d["glowna"], [])
        if d["url"] and all(x["url"] != d["url"] for x in tez[d["glowna"]]):
            tez[d["glowna"]].append({"zrodlo": d["zrodlo"], "url": d["url"]})
    wynik = []
    for w in wiersze:
        o = dict(w)
        o["tez_na"] = [x for x in tez.get(o["id"], []) if x["url"] != o["url"]][:5]
        wynik.append(o)
    return wynik


# ---------------------------------------------------------------------------
# Duplikaty: ta sama oferta w kilku serwisach albo wystawiona ponownie
# (ta sama firma + to samo stanowisko + to samo miasto)
# ---------------------------------------------------------------------------

FIRMA_BEZ_ZNACZENIA = {"sp", "z", "o", "oo", "zoo", "spolka", "spolki", "s", "a", "sa", "k", "sk", "ska",
                       "j", "sj", "p", "spp", "komandytowa", "komandytowo", "akcyjna", "jawna",
                       "partnerska", "ograniczona", "ograniczonej", "odpowiedzialnoscia",
                       "odpowiedzialnosci", "ltd", "llc", "gmbh", "inc", "plc", "ag", "bv", "polska",
                       "poland", "oddzial", "w", "the", "group", "grupa"}
# nazwy, ktore nie mowia, kto naprawde szuka - takich ofert nie laczymy
FIRMA_NIEZNANA = {"", "klient portalu praca pl", "anonimowy pracodawca", "pracodawca", "confidential",
                  "poufne", "anonymous", "firma"}
TYTUL_BEZ_ZNACZENIA = {"k", "m", "x", "f", "d", "km", "mk", "kmx", "mkx", "mfd", "mfx", "fmd", "fm", "mf",
                       "kobieta", "mezczyzna", "all", "genders", "w", "i", "oraz", "and", "z", "na"}
MIASTA = {"warsaw": "warszawa", "cracow": "krakow", "poland": "polska", "remote": "zdalnie",
          "zdalna": "zdalnie", "cala polska": "polska", "caly kraj": "polska"}


def _proste(tekst):
    t = unicodedata.normalize("NFKD", (tekst or "").replace("ł", "l").replace("Ł", "L"))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def klucz_duplikatu(o):
    """Odcisk oferty albo None, gdy danych jest za malo, zeby bezpiecznie laczyc."""
    firma = " ".join(s for s in re.findall(r"[a-z0-9]+", _proste(o.get("firma")))
                     if s not in FIRMA_BEZ_ZNACZENIA)
    if firma in FIRMA_NIEZNANA or " ".join(re.findall(r"[a-z0-9]+", _proste(o.get("firma")))) in FIRMA_NIEZNANA:
        return None
    # "Specjalista / Specjalistka ds. Sprzedazy (k/m)" = "Specjalistka ds. sprzedazy":
    # bez oznaczen plci, kolejnosc i koncowki slow (pierwsze 5 liter) bez znaczenia
    slowa = {s[:5] for s in re.findall(r"[a-z0-9+#]+", _proste(o.get("tytul")))
             if s not in TYTUL_BEZ_ZNACZENIA}
    if not slowa:
        return None
    lok = _proste(o.get("lokalizacja"))
    miasto = re.split(r"[,(;/]| - ", lok)[0].strip()
    miasto = MIASTA.get(miasto, miasto)
    if not miasto and o.get("zdalna"):
        miasto = "zdalnie"
    return "%s|%s|%s" % (firma, " ".join(sorted(slowa)), miasto)


def _dawno(kiedy, dni):
    try:
        t = datetime.strptime((kiedy or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - t > timedelta(days=dni)


def _porzadkuj_duplikaty(conn, kiedy):
    """Laczy duplikaty w bazie. Zwraca liczbe schowanych ofert.

    - oferta, ktora juz oznaczyles (status albo notatka), zostaje; jej kopie
      znikaja z listy - chyba ze status zmieniles ponad 60 dni temu: wtedy
      kopia zostaje jako nowa z dopiskiem "wczesniej: ..."
    - z kopii, ktorych nie ruszales, zostaje najnowsza (zastepuje starsze),
      ale nie liczy sie znowu jako "nowa"
    """
    oferty = [dict(r) for r in conn.execute("""
        SELECT o.*, COALESCE(s.status, 'nowa') AS status, COALESCE(s.notatka, '') AS notatka,
               s.zmieniono AS zmieniono
        FROM oferty o LEFT JOIN statusy s ON s.id = o.id""")]
    grupy = {}
    for o in oferty:
        k = klucz_duplikatu(o)
        if k:
            grupy.setdefault(k, []).append(o)
    schowane = 0
    for grupa in grupy.values():
        if len(grupa) < 2:
            continue
        moje = [o for o in grupa if o["status"] != "nowa" or o["notatka"]]
        wolne = [o for o in grupa if o not in moje]
        if not wolne:
            continue                       # same oznaczone - niczego nie ruszamy
        swieze_moje = [o for o in moje if not _dawno(o["zmieniono"], DNI_DO_PONOWNEGO_POKAZANIA)]
        if swieze_moje:
            glowna = max(swieze_moje, key=lambda o: o["zmieniono"] or "")
            do_schowania = wolne
        else:
            # najnowsza wersja zastepuje starsze (data publikacji, potem pierwsze zobaczenie)
            wolne.sort(key=lambda o: (o["opublikowano"] or "", o["pierwszy_raz"] or ""), reverse=True)
            glowna, do_schowania = wolne[0], wolne[1:]
            if do_schowania:               # ta sama oferta - nie jest "nowa" tylko dlatego, ze odswiezona
                najwczesniej = min(o["pierwszy_raz"] or kiedy for o in wolne)
                conn.execute("UPDATE oferty SET pierwszy_raz=? WHERE id=?", (najwczesniej, glowna["id"]))
            if moje:                       # oznaczona dawno temu - przypominamy przy nowej wersji
                stara = max(moje, key=lambda o: o["zmieniono"] or "")
                conn.execute("UPDATE oferty SET poprzednia=? WHERE id=?", (json.dumps(
                    {"status": stara["status"], "kiedy": (stara["zmieniono"] or "")[:10]}), glowna["id"]))
        for o in do_schowania:
            conn.execute("INSERT INTO duplikaty(id, glowna, zrodlo, url, widziano) VALUES(?, ?, ?, ?, ?) "
                         "ON CONFLICT(id) DO UPDATE SET glowna=excluded.glowna, zrodlo=excluded.zrodlo, "
                         "url=excluded.url, widziano=excluded.widziano",
                         (o["id"], glowna["id"], o["zrodlo"], o["url"], o["ostatnio_widziana"] or kiedy))
            conn.execute("UPDATE duplikaty SET glowna=? WHERE glowna=?", (glowna["id"], o["id"]))
            conn.execute("DELETE FROM oferty WHERE id=?", (o["id"],))
            conn.execute("DELETE FROM szczegoly WHERE id=?", (o["id"],))
            schowane += 1
    return schowane


def porzadkuj_duplikaty(conn):
    with conn:
        return _porzadkuj_duplikaty(conn, teraz())


# ---------------------------------------------------------------------------
# Scalanie swiezych ofert
# ---------------------------------------------------------------------------

def _usun_niepasujace(conn, filtr_profilu, filtr_zasiegu=None):
    moje = {r[0] for r in conn.execute(
        "SELECT id FROM statusy WHERE status != 'nowa' OR notatka != ''")}
    usunietych = 0
    for r in conn.execute("SELECT * FROM oferty").fetchall():
        if r["id"] in moje:
            continue
        d = dict(r)
        if filtr_zasiegu is not None:
            zasieg_ok = filtr_zasiegu(d)
        else:
            zasieg_ok = bool(d.get("warszawa")) or bool(d.get("zdalna"))
        if not zasieg_ok or not filtr_profilu(d)[0]:
            conn.execute("DELETE FROM oferty WHERE id=?", (r["id"],))
            usunietych += 1
    return usunietych


def usun_niepasujace(conn, filtr_profilu, filtr_zasiegu=None):
    """Po zmianie filtrow: usuwa oferty BEZ Twojego statusu i notatki,
    ktore juz nie pasuja. Oferty ze statusem albo notatka zostaja zawsze."""
    with conn:
        return _usun_niepasujace(conn, filtr_profilu, filtr_zasiegu)


def scal(conn, wynik, filtr_profilu=None, filtr_zasiegu=None):
    """Wstawia/aktualizuje oferty. Zwraca (nowych, wygaszonych, usunietych).

    - oferty widziane teraz: aktualizacja danych, aktywna=1
    - oferty niewidziane, ale tylko ze zrodel, ktore odpowiedzialy bez bledow:
      licznik nieobecnosci +1; dopiero po PROG_WYGASNIECIA -> aktywna=0
    - filtr_profilu: oferty bez Twojego statusu, ktore juz nie pasuja do
      filtrow, znikaja z tabeli ofert (statusy nigdy nie sa ruszane)
    """
    kiedy = teraz()
    swieze = wynik["oferty"]
    nowe_id = set()
    with conn:
        istniejace = {r[0] for r in conn.execute("SELECT id FROM oferty")}
        # oferty znane juz jako kopie innej: nie wstawiamy ich, tylko odnotowujemy,
        # ze "glowna" oferta nadal wisi w sieci
        kopie = {r["id"]: r["glowna"] for r in conn.execute("SELECT id, glowna FROM duplikaty")}
        statusy = {r["id"]: r for r in conn.execute("SELECT id, status, notatka, zmieniono FROM statusy")}
        widziane_glowne = set()
        for o in list(swieze.values()):
            glowna = kopie.get(o["id"])
            if glowna is None or o["id"] in istniejace:
                continue
            s = statusy.get(glowna)
            oznaczona = s is not None and (s["status"] != "nowa" or s["notatka"])
            if glowna in istniejace and not (oznaczona and _dawno(s["zmieniono"], DNI_DO_PONOWNEGO_POKAZANIA)):
                conn.execute("UPDATE duplikaty SET widziano=?, url=? WHERE id=?", (kiedy, o.get("url", ""), o["id"]))
                widziane_glowne.add(glowna)
                swieze = {k: v for k, v in swieze.items() if k != o["id"]}
            else:                          # glowna zniknela albo minelo 60 dni - pokazujemy znowu
                conn.execute("DELETE FROM duplikaty WHERE id=?", (o["id"],))
        for g in widziane_glowne:
            conn.execute("UPDATE oferty SET ostatnio_widziana=?, aktywna=1, nieobecnosci=0 WHERE id=?", (kiedy, g))
        for o in swieze.values():
            wartosci = [o.get(p, "") for p in POLA_OFERTY]
            wartosci[POLA_OFERTY.index("zdalna")] = 1 if o.get("zdalna") else 0
            wartosci[POLA_OFERTY.index("warszawa")] = 1 if o.get("warszawa") else 0
            if o["id"] in istniejace:
                ustaw = ", ".join("%s=?" % p for p in POLA_OFERTY[1:])
                conn.execute(
                    "UPDATE oferty SET %s, ostatnio_widziana=?, aktywna=1, nieobecnosci=0 "
                    "WHERE id=?" % ustaw, wartosci[1:] + [kiedy, o["id"]])
            else:
                conn.execute(
                    "INSERT INTO oferty(%s, pierwszy_raz, ostatnio_widziana, aktywna, nieobecnosci) "
                    "VALUES(%s, ?, ?, 1, 0)" % (", ".join(POLA_OFERTY), ", ".join("?" * len(POLA_OFERTY))),
                    wartosci + [kiedy, kiedy])
                nowe_id.add(o["id"])

        duplikatow = _porzadkuj_duplikaty(conn, kiedy)      # polaczone w tym odswiezeniu
        widziane = set(swieze) | widziane_glowne
        nowych = 0
        if nowe_id:   # nowe, ktore przetrwaly laczenie i nie zastapily starszej wersji
            for r in conn.execute("SELECT id, pierwszy_raz FROM oferty WHERE id IN (%s)" %
                                  ", ".join("?" * len(nowe_id)), list(nowe_id)):
                if r["pierwszy_raz"] == kiedy:
                    nowych += 1

        wygaszonych = 0
        udane = wynik.get("udane_zrodla") or []
        if udane:
            znaki = ", ".join("?" * len(udane))
            niewidziane = [r for r in conn.execute(
                "SELECT id, nieobecnosci, aktywna FROM oferty WHERE zrodlo IN (%s)" % znaki, udane)
                if r["id"] not in widziane]
            for r in niewidziane:
                n = (r["nieobecnosci"] or 0) + 1
                aktywna = 0 if n >= PROG_WYGASNIECIA else r["aktywna"]
                if r["aktywna"] and not aktywna:
                    wygaszonych += 1
                conn.execute("UPDATE oferty SET nieobecnosci=?, aktywna=? WHERE id=?",
                             (n, aktywna, r["id"]))

        usunietych = 0
        if filtr_profilu is not None:
            usunietych = _usun_niepasujace(conn, filtr_profilu, filtr_zasiegu)

        if udane or swieze:   # nie udajemy odswiezenia, jesli nic nie odpowiedzialo
            conn.execute("INSERT INTO meta(klucz, wartosc) VALUES('ostatnie_odswiezenie', ?) "
                         "ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc", (kiedy,))
        conn.execute("INSERT INTO meta(klucz, wartosc) VALUES('bledy', ?) "
                     "ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc",
                     (json.dumps(wynik.get("bledy") or [], ensure_ascii=False),))
    wynik["duplikatow"] = duplikatow
    return nowych, wygaszonych, usunietych


# ---------------------------------------------------------------------------
# Import ze starej wersji (oferty.json) i kopie zapasowe
# ---------------------------------------------------------------------------

def importuj_json(conn, sciezka_json):
    """Jednorazowe przeniesienie danych ze starej wersji aplikacji."""
    with open(sciezka_json, "r", encoding="utf-8") as f:
        dane = json.load(f)
    ile = 0
    with conn:
        for o in dane.get("oferty", {}).values():
            wartosci = [o.get(p, "") for p in POLA_OFERTY]
            wartosci[POLA_OFERTY.index("zdalna")] = 1 if o.get("zdalna") else 0
            wartosci[POLA_OFERTY.index("warszawa")] = 1 if o.get("warszawa") else 0
            conn.execute(
                "INSERT OR IGNORE INTO oferty(%s, pierwszy_raz, ostatnio_widziana, aktywna, nieobecnosci) "
                "VALUES(%s, ?, ?, ?, 0)" % (", ".join(POLA_OFERTY), ", ".join("?" * len(POLA_OFERTY))),
                wartosci + [o.get("pierwszy_raz"), o.get("ostatnio_widziana"),
                            1 if o.get("aktywna", True) else 0])
            if (o.get("status") or "nowa") != "nowa" or o.get("notatka"):
                conn.execute(
                    "INSERT OR IGNORE INTO statusy(id, status, notatka, zmieniono) VALUES(?, ?, ?, ?)",
                    (o["id"], o.get("status") or "nowa", o.get("notatka") or "", teraz()))
            ile += 1
        if dane.get("ostatnie_odswiezenie"):
            conn.execute("INSERT OR IGNORE INTO meta(klucz, wartosc) VALUES('ostatnie_odswiezenie', ?)",
                         (dane["ostatnie_odswiezenie"],))
    return ile


def wyczysc_oferty(conn, katalog_kopii=None):
    """"Zacznij od zera": oferty, statusy, notatki, historia, opisy i CV znikaja.
    Ustawienia wyszukiwania zostaja. oferty.json nie bedzie juz importowany.
    Wczesniej robi kopie kopie/przed-czyszczeniem-<data>.db."""
    if katalog_kopii:
        os.makedirs(katalog_kopii, exist_ok=True)
        cel = sqlite3.connect(os.path.join(
            katalog_kopii, "przed-czyszczeniem-%s.db" % datetime.now().strftime("%Y-%m-%d-%H%M%S")))
        with cel:
            conn.backup(cel)
        cel.close()
    with conn:
        for tabela in ("oferty", "statusy", "historia", "szczegoly", "duplikaty"):
            conn.execute("DELETE FROM %s" % tabela)
        # dane poprzedniej osoby: CV, filtry listy, stan LinkedIn
        conn.execute("DELETE FROM meta WHERE klucz IN ('ostatnie_odswiezenie', 'bledy', 'cv', "
                     "'filtry', 'linkedin_ostatnio', 'linkedin_pauza_do')")
        conn.execute("INSERT INTO meta(klucz, wartosc) VALUES('bez_importu_json', '1') "
                     "ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc")


def kopia_zapasowa(conn, katalog, zostaw=14, nazwa=None):
    """Jedna kopia bazy dziennie w podfolderze 'kopie', trzymamy 14 ostatnich.
    nazwa: jednorazowa kopia przed wieksza zmiana (np. "przed-duplikatami")."""
    os.makedirs(katalog, exist_ok=True)
    if nazwa:
        plik = os.path.join(katalog, "%s-%s.db" % (nazwa, datetime.now().strftime("%Y-%m-%d-%H%M%S")))
        cel = sqlite3.connect(plik)
        with cel:
            conn.backup(cel)
        cel.close()
        return plik
    plik = os.path.join(katalog, "radar-%s.db" % datetime.now().strftime("%Y-%m-%d"))
    if not os.path.exists(plik):
        cel = sqlite3.connect(plik)
        with cel:
            conn.backup(cel)
        cel.close()
    kopie = sorted(f for f in os.listdir(katalog) if f.startswith("radar-") and f.endswith(".db"))
    for stara in kopie[:-zostaw]:
        try:
            os.remove(os.path.join(katalog, stara))
        except OSError:
            pass
    return plik
