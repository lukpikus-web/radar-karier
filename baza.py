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
import sqlite3
from datetime import datetime, timezone

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
"""

# ile odswiezen z rzedu oferta musi byc nieobecna, zeby uznac ja za wygasla
PROG_WYGASNIECIA = 2


def teraz():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def otworz(sciezka):
    conn = sqlite3.connect(sciezka, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(SCHEMAT)
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
    return [dict(w) for w in wiersze]


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
    nowych = 0
    with conn:
        istniejace = {r[0] for r in conn.execute("SELECT id FROM oferty")}
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
                nowych += 1

        wygaszonych = 0
        udane = wynik.get("udane_zrodla") or []
        if udane:
            znaki = ", ".join("?" * len(udane))
            niewidziane = [r for r in conn.execute(
                "SELECT id, nieobecnosci, aktywna FROM oferty WHERE zrodlo IN (%s)" % znaki, udane)
                if r["id"] not in swieze]
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
        for tabela in ("oferty", "statusy", "historia", "szczegoly"):
            conn.execute("DELETE FROM %s" % tabela)
        # dane poprzedniej osoby: CV, filtry listy, stan LinkedIn
        conn.execute("DELETE FROM meta WHERE klucz IN ('ostatnie_odswiezenie', 'bledy', 'cv', "
                     "'filtry', 'linkedin_ostatnio', 'linkedin_pauza_do')")
        conn.execute("INSERT INTO meta(klucz, wartosc) VALUES('bez_importu_json', '1') "
                     "ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc")


def kopia_zapasowa(conn, katalog, zostaw=14):
    """Jedna kopia bazy dziennie w podfolderze 'kopie', trzymamy 14 ostatnich."""
    os.makedirs(katalog, exist_ok=True)
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
