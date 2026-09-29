# -*- coding: utf-8 -*-
"""
Radar Karier - serwer aplikacji (dziala tylko na Twoim komputerze).

Okno aplikacji to strona HTML (folder "ui") wyswietlana przez Microsoft Edge
albo Chrome w trybie aplikacji - rysuje ja karta graficzna, wiec przewijanie
jest plynne. Ten plik:
  - trzyma baze radar.db (oferty, statusy, notatki, ustawienia, CV),
  - pobiera oferty (radar.py) i opisy ofert, liczy dopasowanie do CV,
  - odpowiada oknu na adresie http://127.0.0.1:<port> - tylko z tego komputera.

Uruchamianie: "Radar Karier.pyw" (dwuklik).
"""

import hashlib
import json
import logging
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KATALOG = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, KATALOG)

PLIK_LOGU = os.path.join(KATALOG, "radar.log")
PLIK_BAZY = os.path.join(KATALOG, "radar.db")
PLIK_JSON = os.path.join(KATALOG, "oferty.json")
KATALOG_KOPII = os.path.join(KATALOG, "kopie")
KATALOG_UI = os.path.join(KATALOG, "ui")
PLIK_PORTU = os.path.join(KATALOG, ".radar-okno")       # port+klucz dzialajacej aplikacji
PROFIL_OKNA = os.path.join(KATALOG, ".okno-przegladarki")  # osobny profil Edge/Chrome

try:
    if os.path.exists(PLIK_LOGU) and os.path.getsize(PLIK_LOGU) > 1_000_000:
        os.replace(PLIK_LOGU, PLIK_LOGU + ".stary")
except OSError:
    pass
_handler = logging.FileHandler(PLIK_LOGU, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s"))
LOG = logging.getLogger("radar")
LOG.setLevel(logging.INFO)
LOG.addHandler(_handler)

import baza          # noqa: E402
import dopasowanie   # noqa: E402
import radar         # noqa: E402

BEZ_SYGNALU_S = 180       # nikt sie nie odzywa tyle sekund -> zamykamy aplikacje
                          # (zminimalizowane okno Edge odzywa sie nawet raz na minute)
PO_ZAMKNIECIU_S = 8       # okno zglosilo zamkniecie -> konczymy, jesli nic nie wroci
PORT_WIFI = 8765          # adres dla telefonu: http://<adres-komputera>:8765
PROBY_PIN = 5             # tyle blednych PIN-ow z jednego telefonu...
BLOKADA_PIN_S = 15 * 60   # ...i blokada na 15 minut


# ---------------------------------------------------------------------------
# Stan aplikacji (jedna baza, jedna blokada - zapisy nigdy sie nie krzyzuja)
# ---------------------------------------------------------------------------

class Aplikacja:

    def __init__(self):
        self.blokada = threading.RLock()
        self.conn = baza.otworz(PLIK_BAZY)
        if (baza.liczba_ofert(self.conn) == 0 and os.path.exists(PLIK_JSON)
                and not baza.meta_get(self.conn, "bez_importu_json")):
            ile = baza.importuj_json(self.conn, PLIK_JSON)
            LOG.info("Zaimportowano %d ofert z oferty.json", ile)
        self.ustawienia = radar.zastosuj_ustawienia(self._json_meta("ustawienia"))
        try:
            baza.kopia_zapasowa(self.conn, KATALOG_KOPII)
        except Exception:
            LOG.exception("Kopia zapasowa nie wyszla")
        cv = self._json_meta("cv")
        self.cv = cv if isinstance(cv, dict) and (cv.get("tekst") or cv.get("umiejetnosci")) else None
        self.profil = None
        self.dopasowania = {}
        self.wersja = 0                  # rosnie przy kazdej zmianie danych - okno wie, kiedy przeladowac
        # pobieranie ofert
        self.watek = None
        self.przerwij = threading.Event()
        self.postep = {"trwa": False, "log": "", "koniec": ""}
        self.start_odswiezania = 0.0
        # opisy ofert (szczegoly) - po kolei, w osobnym watku
        self.kolejka_szczeg = queue.Queue()
        self.pobierane = set()
        self.bledy_szczeg = {}
        threading.Thread(target=self._pracownik_szczegolow, daemon=True).start()
        self.ostatni_sygnal = time.time()
        self.okno_zamkniete = 0.0
        self.serwer_wifi = None
        self.blad_wifi = ""
        self.nieudane_piny = {}
        self._przelicz_dopasowania()

    def _json_meta(self, klucz):
        try:
            return json.loads(baza.meta_get(self.conn, klucz) or "null")
        except ValueError:
            LOG.warning("Uszkodzone dane '%s' w bazie - pomijam", klucz)
            return None

    def zmiana(self):
        self.wersja += 1

    # --- dane dla okna ---------------------------------------------------
    def stan(self):
        with self.blokada:
            oferty = baza.wszystkie(self.conn)
            szczegoly = baza.wszystkie_szczegoly(self.conn)
            return {
                "wersja": self.wersja,
                "oferty": oferty,
                "szczegoly": szczegoly,
                "dopasowania": self.dopasowania if self.cv else {},
                "ustawienia": self.ustawienia,
                "cv": self.cv,
                "filtry": self._json_meta("filtry"),
                "ostatnie_odswiezenie": baza.meta_get(self.conn, "ostatnie_odswiezenie"),
                "poziomy": [[k, n] for k, n, _ in radar.POZIOMY],
                "zrodla": radar.ZRODLA,
                "zrodla_fraz": radar.ZRODLA_FRAZ,
                "linkedin": {"max_zapytan": radar.LINKEDIN_MAX_ZAPYTAN,
                             "co_ile_godzin": radar.LINKEDIN_CO_ILE_GODZIN},
                "pobierane": sorted(self.pobierane),
                "bledy_szczeg": self.bledy_szczeg,
                "postep": self.postep,
            }

    def _przelicz_dopasowania(self):
        self.dopasowania, self.profil = {}, None
        if not self.cv:
            return
        with self.blokada:
            oferty = baza.wszystkie(self.conn)
            szczegoly = baza.wszystkie_szczegoly(self.conn)
        teksty = [" ".join([o.get("tytul") or "", o.get("opis") or ""] +
                           [" ".join(v) for v in (szczegoly.get(o["id"]) or {}).values()
                            if isinstance(v, list)]) for o in oferty]
        try:
            self.profil = dopasowanie.Profil(self.cv.get("tekst", ""), self.cv.get("umiejetnosci", []),
                                             teksty)
            self.dopasowania = {o["id"]: self.profil.ocen(o, szczegoly.get(o["id"])) for o in oferty}
        except Exception:
            LOG.exception("Dopasowanie do CV nie wyszlo")

    # --- statusy, notatki, filtry -------------------------------------------
    def status(self, ident, kod):
        with self.blokada:
            kiedy = baza.ustaw_status(self.conn, ident, kod)
        self.zmiana()
        return {"zmieniono": kiedy}

    def notatka(self, ident, tekst):
        with self.blokada:
            kiedy = baza.ustaw_notatke(self.conn, ident, tekst)
        return {"zmieniono": kiedy}

    def filtry(self, f):
        with self.blokada:
            baza.meta_set(self.conn, "filtry", json.dumps(f, ensure_ascii=False))
        return {}

    # --- ustawienia ------------------------------------------------------
    def zapisz_ustawienia(self, u):
        stare = self.ustawienia
        try:
            u = radar.zastosuj_ustawienia(u)
            with self.blokada:
                baza.meta_set(self.conn, "ustawienia", json.dumps(u, ensure_ascii=False))
                usunietych = baza.usun_niepasujace(self.conn, radar.pasuje_do_profilu,
                                                   radar.pasuje_zasieg)
        except Exception:
            radar.zastosuj_ustawienia(stare)
            raise
        self.ustawienia = u
        LOG.info("Zapisano ustawienia: %d fraz, miasto %r, usunieto %d niepasujacych ofert",
                 len(u["frazy"]), u["miasto"], usunietych)
        if usunietych:
            self._przelicz_dopasowania()
        self.zmiana()
        return {"usunietych": usunietych, "ustawienia": u}

    def domyslne_ustawienia(self):
        return radar.domyslne_ustawienia()

    def zacznij_od_zera(self):
        if self.watek and self.watek.is_alive():
            raise BladUzytkownika("Poczekaj, aż skończy się pobieranie ofert.")
        with self.blokada:
            baza.wyczysc_oferty(self.conn, KATALOG_KOPII)
        self.cv, self.bledy_szczeg = None, {}
        self._przelicz_dopasowania()
        LOG.info("Baza wyczyszczona (zacznij od zera)")
        self.zmiana()
        return {}

    # --- CV -----------------------------------------------------------------
    def wczytaj_cv(self, nazwa, dane):
        rozszerzenie = os.path.splitext(nazwa or "")[1].lower() or ".txt"
        tymczasowy = os.path.join(KATALOG, ".cv-tymczasowe" + rozszerzenie)
        try:
            with open(tymczasowy, "wb") as f:
                f.write(dane)
            tekst = dopasowanie.wczytaj_cv(tymczasowy)
        except dopasowanie.BladCV as e:
            raise BladUzytkownika(str(e)[:1].upper() + str(e)[1:] + ".")
        finally:
            try:
                os.remove(tymczasowy)
            except OSError:
                pass
        return {"tekst": tekst, "umiejetnosci": dopasowanie.wykryj_umiejetnosci(tekst)}

    def wykryj(self, tekst, obecne):
        znalezione = dopasowanie.wykryj_umiejetnosci(tekst, obecne)
        kanoniczne = {dopasowanie.kanoniczna(u).lower() for u in obecne}
        return {"nowe": [u for u in znalezione if u.lower() not in kanoniczne]}

    def zapisz_cv(self, cv):
        with self.blokada:
            if not cv:
                baza.meta_set(self.conn, "cv", "")
            else:
                cv = {"tekst": cv.get("tekst", ""), "umiejetnosci": cv.get("umiejetnosci", []),
                      "plik": cv.get("plik", ""), "zapisano": baza.teraz()}
                if cv["tekst"] and not cv["umiejetnosci"]:
                    cv["umiejetnosci"] = dopasowanie.wykryj_umiejetnosci(cv["tekst"])
                baza.meta_set(self.conn, "cv", json.dumps(cv, ensure_ascii=False))
        self.cv = cv or None
        self._przelicz_dopasowania()
        dodane = self.dodaj_frazy_z_cv() if self.cv else []
        LOG.info("CV %s", "zapisane" if self.cv else "usuniete")
        self.zmiana()
        return {"dodane": dodane}

    def dodaj_frazy_z_cv(self, wymus=False):
        """Frazy wyszukiwania z CV do ustawien (kategoria "Z CV"); stare "Z CV" wymienia."""
        if not self.cv:
            return []
        tekst = self.cv.get("tekst", "")
        umiejetnosci = self.cv.get("umiejetnosci", [])
        klucz = hashlib.md5((tekst + "|" + "|".join(umiejetnosci)).encode("utf-8")).hexdigest()
        u = json.loads(json.dumps(self.ustawienia))
        if u.get("cv_klucz") == klucz and not wymus:
            return []
        frazy = [f for f in u["frazy"] if not f.get("z_cv")]
        istniejace = {dopasowanie.uprosc(f["fraza"]) for f in frazy}
        limit = radar.LINKEDIN_MAX_ZAPYTAN // max(1, len(radar.LINKEDIN_LOKALIZACJE))
        w_linkedin = sum("LinkedIn" in f.get("zrodla", []) for f in frazy)
        dodane = []
        for fraza, typ in dopasowanie.frazy_z_cv(tekst, umiejetnosci):
            if dopasowanie.uprosc(fraza) in istniejace:
                continue
            zrodla = ["pracuj.pl", "RocketJobs", "OLX"]
            if typ == "stanowisko" and w_linkedin < limit:
                zrodla.insert(1, "LinkedIn")
                w_linkedin += 1
            frazy.append({"fraza": fraza, "kategoria": "Z CV", "zrodla": zrodla, "z_cv": True})
            dodane.append(fraza)
        u["frazy"], u["cv_klucz"] = frazy, klucz
        u = radar.zastosuj_ustawienia(u)
        with self.blokada:
            baza.meta_set(self.conn, "ustawienia", json.dumps(u, ensure_ascii=False))
        self.ustawienia = u
        LOG.info("Frazy z CV: %s", ", ".join(dodane) or "(brak nowych)")
        return dodane

    # --- opisy ofert (obowiazki, wymagania) ----------------------------------
    def pobierz_szczegoly(self, ident):
        with self.blokada:
            oferta = next((o for o in baza.wszystkie(self.conn) if o["id"] == ident), None)
        if not oferta or not oferta.get("url"):
            raise BladUzytkownika("Ta oferta nie ma linku.")
        if ident not in self.pobierane:
            self.bledy_szczeg.pop(ident, None)
            self.pobierane.add(ident)
            self.kolejka_szczeg.put((ident, oferta))
        return {}

    def _pracownik_szczegolow(self):
        while True:
            ident, oferta = self.kolejka_szczeg.get()
            dane = blad = None
            try:
                dane = radar.pobierz_szczegoly(oferta)
            except urllib.error.HTTPError as e:
                blad = "serwis odpowiedział błędem %d" % e.code
                if e.code in (404, 410):
                    blad += " (oferta mogła wygasnąć)"
            except urllib.error.URLError:
                blad = "brak połączenia z serwisem"
            except TimeoutError:
                blad = "serwis nie odpowiada"
            except Exception as e:
                blad = str(e) or e.__class__.__name__
            if dane:
                with self.blokada:
                    dane["pobrano"] = baza.zapisz_szczegoly(self.conn, ident, dane)
                if self.profil:
                    self.dopasowania[ident] = self.profil.ocen(oferta, dane)
                LOG.info("Szczegoly oferty %s (%s)", ident, dane.get("metoda"))
            else:
                self.bledy_szczeg[ident] = blad
                LOG.warning("Szczegoly oferty %s (%s) nie wyszly: %s", ident, oferta.get("url"), blad)
            self.pobierane.discard(ident)
            self.zmiana()
            time.sleep(0.5)

    # --- dostep z telefonu przez Wi-Fi --------------------------------------
    def wifi_stan(self):
        wl = self.serwer_wifi is not None
        return {"wlaczone": wl, "pin": baza.meta_get(self.conn, "wifi_pin") if wl else "",
                "adresy": ["http://%s:%d" % (ip, self.serwer_wifi.server_address[1])
                           for ip in adresy_komputera()] if wl else [],
                "blad": self.blad_wifi, "publiczna": wl and siec_publiczna()}

    def wifi(self, wlacz, nowy_pin=False):
        with self.blokada:
            if nowy_pin or not baza.meta_get(self.conn, "wifi_pin"):
                baza.meta_set(self.conn, "wifi_pin", "%06d" % secrets.randbelow(10 ** 6))
                baza.meta_set(self.conn, "wifi_klucz", secrets.token_urlsafe(24))   # wylogowuje telefony
            baza.meta_set(self.conn, "wifi", "1" if wlacz else "")
        if wlacz:
            self.uruchom_wifi()
        else:
            self.zatrzymaj_wifi()
        return self.wifi_stan()

    def uruchom_wifi(self):
        self.blad_wifi = ""
        if self.serwer_wifi is not None:
            return
        for port in range(PORT_WIFI, PORT_WIFI + 10):
            try:
                srv = ThreadingHTTPServer(("0.0.0.0", port), Obsluga)
                break
            except OSError:
                continue
        else:
            self.blad_wifi = "nie udało się otworzyć portu %d-%d" % (PORT_WIFI, PORT_WIFI + 9)
            LOG.warning("Wi-Fi: %s", self.blad_wifi)
            return
        srv.daemon_threads = True
        srv.zdalny = True
        self.serwer_wifi = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        LOG.info("Dostep z telefonu wlaczony: port %d", srv.server_address[1])

    def zatrzymaj_wifi(self):
        srv, self.serwer_wifi = self.serwer_wifi, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()
            LOG.info("Dostep z telefonu wylaczony")

    def zaloguj_telefon(self, pin, ip):
        teraz = time.time()
        proby, do = self.nieudane_piny.get(ip, (0, 0))
        if teraz < do:
            raise BladUzytkownika("Za dużo błędnych prób. Spróbuj za %d min." % ((do - teraz) // 60 + 1))
        with self.blokada:
            dobry = baza.meta_get(self.conn, "wifi_pin") or ""
            klucz = baza.meta_get(self.conn, "wifi_klucz") or ""
        if dobry and secrets.compare_digest(str(pin).strip(), dobry):
            self.nieudane_piny.pop(ip, None)
            LOG.info("Telefon %s zalogowany", ip)
            return {"klucz": klucz}
        proby += 1
        self.nieudane_piny[ip] = (0, teraz + BLOKADA_PIN_S) if proby >= PROBY_PIN else (proby, 0)
        LOG.warning("Bledny PIN z %s (%d)", ip, proby)
        raise BladUzytkownika("Zły PIN." if proby < PROBY_PIN else
                              "Za dużo błędnych prób. Spróbuj za %d min." % (BLOKADA_PIN_S // 60))

    # --- pobieranie ofert ----------------------------------------------------
    def _pomin_linkedin(self):
        if not radar.WLACZONE_ZRODLA.get("LinkedIn"):
            return {}
        teraz = time.time()

        def czas(klucz):
            try:
                return float(baza.meta_get(self.conn, klucz) or 0)
            except ValueError:
                return 0.0
        godzina = lambda t: datetime.fromtimestamp(t).strftime("%H:%M" if t - teraz < 20 * 3600
                                                                  else "%d.%m %H:%M")
        with self.blokada:
            pauza_do, ostatnio = czas("linkedin_pauza_do"), czas("linkedin_ostatnio")
        if teraz < pauza_do:
            return {"LinkedIn": "wcześniej odmówił dostępu, przerwa do %s" % godzina(pauza_do)}
        nastepny = ostatnio + radar.LINKEDIN_CO_ILE_GODZIN * 3600
        if teraz < nastepny:
            return {"LinkedIn": "był pytany niedawno, następny raz po %s" % godzina(nastepny)}
        return {}

    def odswiez(self):
        if self.watek and self.watek.is_alive():
            return {"trwa": True}
        if not self.ustawienia.get("frazy") and self.cv:
            self.dodaj_frazy_z_cv(wymus=True)
        if not self.ustawienia.get("frazy") and not radar.WLACZONE_ZRODLA.get("WorkConnect"):
            raise BladUzytkownika("BRAK_FRAZ")
        pomin = self._pomin_linkedin()
        self.przerwij.clear()
        self.start_odswiezania = time.time()
        self.postep = {"trwa": True, "log": "Pobieram świeże oferty…", "koniec": ""}

        def log(m):
            LOG.info(m)
            self.postep["log"] = m

        def praca():
            try:
                wynik = radar.zbierz_wszystko(log=log, przerwij=self.przerwij, pomin=pomin)
                self._po_odswiezeniu(wynik)
            except Exception:
                LOG.error(traceback.format_exc())
                self.postep = {"trwa": False, "log": "",
                               "koniec": "Odświeżanie nie wyszło - szczegóły w radar.log"}
                self.zmiana()

        self.watek = threading.Thread(target=praca, daemon=True)
        self.watek.start()
        self.zmiana()
        return {"trwa": True}

    def _po_odswiezeniu(self, wynik):
        with self.blokada:
            try:
                if "LinkedIn" in (wynik.get("pytane_zrodla") or []):
                    baza.meta_set(self.conn, "linkedin_ostatnio", str(self.start_odswiezania))
                if "LinkedIn" in (wynik.get("odmowy") or []):
                    baza.meta_set(self.conn, "linkedin_pauza_do",
                                  str(time.time() + radar.LINKEDIN_PAUZA_GODZIN * 3600))
            except Exception:
                LOG.exception("Zapis stanu LinkedIn nie wyszedl")
            nowych, wygaszonych, _ = baza.scal(self.conn, wynik, radar.pasuje_do_profilu,
                                               radar.pasuje_zasieg)
        for tytul, powod in wynik.get("odrzucone", [])[:30]:
            LOG.info("odrzucona: %s [%s]", tytul, powod)
        tekst = "Gotowe: %d nowych, %d wygasło" % (nowych, wygaszonych)
        if "LinkedIn" in (wynik.get("odmowy") or []):
            tekst += "  ·  LinkedIn ograniczył dostęp - spróbuję za %d h" % radar.LINKEDIN_PAUZA_GODZIN
        elif radar.WLACZONE_ZRODLA.get("LinkedIn") and "LinkedIn" not in (wynik.get("pytane_zrodla") or []):
            powod = self._pomin_linkedin().get("LinkedIn")
            if powod:
                tekst += "  ·  LinkedIn pominięty: " + powod
        if wynik.get("bledy"):
            tekst += "  ·  błędów: %d (szczegóły w radar.log)" % len(wynik["bledy"])
            for b in wynik["bledy"]:
                LOG.warning(b)
        self._przelicz_dopasowania()
        self.postep = {"trwa": False, "log": "", "koniec": tekst}
        self.zmiana()


def adresy_komputera():
    """Adresy tego komputera w sieci domowej (np. 192.168.1.23)."""
    adresy = []
    try:                                   # adres, przez ktory komputer wychodzi do sieci
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # nic nie jest wysylane
            adresy.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in adresy:
                adresy.append(ip)
    except OSError:
        pass
    prywatne = [ip for ip in adresy if not ip.startswith("127.") and
                (ip.startswith(("192.168.", "10.")) or
                 (ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31))]
    return prywatne or [ip for ip in adresy if not ip.startswith("127.")]


def siec_publiczna():
    """Czy Windows uznal obecna siec za publiczna? Wtedy zapora zwykle blokuje telefon
    (np. gdy laptop korzysta z hotspotu telefonu - nowa siec jest domyslnie publiczna)."""
    if os.name != "nt":
        return False
    try:
        wynik = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                "(Get-NetConnectionProfile).NetworkCategory"],
                               capture_output=True, text=True, timeout=10, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        return False
    return "Public" in wynik.stdout


def otworz_link(url):
    """Oferta otwiera sie w Twojej zwyklej przegladarce (tam, gdzie jestes zalogowany)."""
    if not url.startswith(("http://", "https://")):
        raise BladUzytkownika("To nie jest adres strony.")
    webbrowser.open(url, new=2)
    return {}


class BladUzytkownika(Exception):
    """Komunikat do pokazania w oknie (nie awaria)."""


# ---------------------------------------------------------------------------
# Serwer HTTP - tylko 127.0.0.1 i tylko z kluczem (inne strony w przegladarce
# nie moga wysylac polecen do aplikacji)
# ---------------------------------------------------------------------------

TYPY = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".ico": "image/x-icon", ".json": "application/json"}


class Obsluga(BaseHTTPRequestHandler):
    app = None
    klucz = ""
    server_version = "RadarKarier"

    def log_message(self, *args):          # bez spamu w konsoli / logu
        pass

    def _wyslij(self, kod, tresc, typ="application/json; charset=utf-8"):
        dane = tresc if isinstance(tresc, bytes) else json.dumps(tresc, ensure_ascii=False).encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(dane)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(dane)
        except (BrokenPipeError, ConnectionResetError):
            pass                           # okno zamkniete w trakcie - nic sie nie stalo

    def _zdalny(self):
        return getattr(self.server, "zdalny", False)

    def _dozwolone(self):
        podany = self.headers.get("X-Klucz", "")
        if self._zdalny():                     # telefon: klucz z logowania PIN-em
            with self.app.blokada:
                klucz = baza.meta_get(self.app.conn, "wifi_klucz") or ""
            return bool(klucz) and secrets.compare_digest(podany, klucz)
        return secrets.compare_digest(podany, self.klucz)

    def do_GET(self):
        sciezka = self.path.split("?", 1)[0]
        if sciezka == "/api/stan":
            if not self._dozwolone():
                return self._wyslij(403, {"blad": "brak dostępu"})
            self.app.ostatni_sygnal = time.time()
            return self._wyslij(200, self.app.stan())
        if sciezka == "/api/wersja":
            if not self._dozwolone():
                return self._wyslij(403, {"blad": "brak dostępu"})
            self.app.ostatni_sygnal = time.time()
            return self._wyslij(200, {"wersja": self.app.wersja, "postep": self.app.postep,
                                      "pobierane": sorted(self.app.pobierane)})
        if sciezka == "/api/zyje":             # dla drugiego uruchomienia: czy aplikacja dziala?
            return self._wyslij(200, {"ok": True, "zdalny": self._zdalny()})
        # pliki okna (ui/)
        if sciezka in ("/", ""):
            sciezka = "/index.html"
        pelna = os.path.normpath(os.path.join(KATALOG_UI, sciezka.lstrip("/")))
        if not pelna.startswith(KATALOG_UI) or not os.path.isfile(pelna):
            return self._wyslij(404, b"nie ma", "text/plain")
        with open(pelna, "rb") as f:
            tresc = f.read()
        return self._wyslij(200, tresc, TYPY.get(os.path.splitext(pelna)[1], "application/octet-stream"))

    def do_POST(self):
        polecenie = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
        if polecenie == "zamykam" and not self._zdalny():  # okno na komputerze sie zamyka
            dlugosc = min(int(self.headers.get("Content-Length") or 0), 1000)
            podany = self.rfile.read(dlugosc).decode("utf-8", "replace").strip()
            if secrets.compare_digest(podany, self.klucz):
                self.app.okno_zamkniete = time.time()
            return self._wyslij(200, {})
        if polecenie == "zaloguj" and self._zdalny():     # telefon podaje PIN
            try:
                dlugosc = min(int(self.headers.get("Content-Length") or 0), 1000)
                d = json.loads(self.rfile.read(dlugosc).decode("utf-8") or "{}")
                return self._wyslij(200, self.app.zaloguj_telefon(d.get("pin", ""), self.client_address[0]))
            except BladUzytkownika as e:
                return self._wyslij(403, {"blad": str(e)})
            except ValueError:
                return self._wyslij(400, {"blad": "złe dane"})
        if not self._dozwolone():
            return self._wyslij(403, {"blad": "brak dostępu"})
        if self._zdalny() and polecenie in ("wifi", "wifi_stan", "zacznij_od_zera", "otworz"):
            return self._wyslij(403, {"blad": "To można zrobić tylko na komputerze."})
        self.app.ostatni_sygnal = time.time()
        dlugosc = int(self.headers.get("Content-Length") or 0)
        if dlugosc > 20_000_000:
            return self._wyslij(413, {"blad": "plik jest za duży"})
        surowe = self.rfile.read(dlugosc) if dlugosc else b""
        a = self.app
        try:
            if polecenie == "cv_plik":             # tresc pliku CV w ciele zapytania
                nazwa = urllib.request.unquote(self.headers.get("X-Nazwa", "cv.txt"))
                return self._wyslij(200, a.wczytaj_cv(nazwa, surowe))
            d = json.loads(surowe.decode("utf-8") or "{}")
            akcje = {
                "status": lambda: a.status(d["id"], d["status"]),
                "notatka": lambda: a.notatka(d["id"], d.get("tekst", "")),
                "filtry": lambda: a.filtry(d.get("filtry") or {}),
                "ustawienia": lambda: a.zapisz_ustawienia(d["ustawienia"]),
                "domyslne": lambda: {"ustawienia": a.domyslne_ustawienia()},
                "zacznij_od_zera": a.zacznij_od_zera,
                "wykryj": lambda: a.wykryj(d.get("tekst", ""), d.get("obecne", [])),
                "cv": lambda: a.zapisz_cv(d.get("cv")),
                "szczegoly": lambda: a.pobierz_szczegoly(d["id"]),
                "odswiez": a.odswiez,
                "przerwij": lambda: (a.przerwij.set(), {})[1],
                "otworz": lambda: otworz_link(d.get("url", "")),
                "wifi_stan": a.wifi_stan,
                "wifi": lambda: a.wifi(bool(d.get("wlacz")), bool(d.get("nowy_pin"))),
            }
            if polecenie not in akcje:
                return self._wyslij(404, {"blad": "nieznane polecenie"})
            return self._wyslij(200, akcje[polecenie]())
        except BladUzytkownika as e:
            return self._wyslij(400, {"blad": str(e)})
        except Exception as e:
            LOG.error("Polecenie %s nie wyszlo:\n%s", polecenie, traceback.format_exc())
            return self._wyslij(500, {"blad": "%s (szczegóły w radar.log)" % e})


# ---------------------------------------------------------------------------
# Okno: Edge albo Chrome w trybie aplikacji, a gdy ich brak - domyslna przegladarka
# ---------------------------------------------------------------------------

def _znajdz_przegladarke():
    kandydaci = []
    for zmienna in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        baza_sciezki = os.environ.get(zmienna)
        if baza_sciezki:
            kandydaci.append(os.path.join(baza_sciezki, "Microsoft", "Edge", "Application", "msedge.exe"))
    for zmienna in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        baza_sciezki = os.environ.get(zmienna)
        if baza_sciezki:
            kandydaci.append(os.path.join(baza_sciezki, "Google", "Chrome", "Application", "chrome.exe"))
    for nazwa in ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        sciezka = shutil.which(nazwa)
        if sciezka:
            kandydaci.append(sciezka)
    return next((k for k in kandydaci if k and os.path.isfile(k)), None)


def otworz_okno(adres):
    przegladarka = _znajdz_przegladarke()
    if przegladarka:
        try:
            flagi = 0x08000000 if sys.platform.startswith("win") else 0   # bez czarnej konsoli
            subprocess.Popen([przegladarka, "--app=" + adres, "--user-data-dir=" + PROFIL_OKNA,
                              "--no-first-run", "--no-default-browser-check",
                              # zminimalizowane / zasloniete okno ma dalej dawac znak zycia
                              "--disable-background-timer-throttling",
                              "--disable-backgrounding-occluded-windows",
                              "--disable-renderer-backgrounding",
                              "--window-size=1240,900"], creationflags=flagi)
            LOG.info("Okno: %s", os.path.basename(przegladarka))
            return
        except OSError:
            LOG.exception("Nie udalo sie uruchomic %s", przegladarka)
    LOG.info("Okno: domyslna przegladarka")
    webbrowser.open(adres)


def _komunikat(tekst):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, tekst, "Radar Karier", 0x10)
    except Exception:
        print(tekst)


def main():
    # juz dziala? - otworz tylko nowe okno
    try:
        with open(PLIK_PORTU, encoding="utf-8") as f:
            port, klucz = f.read().split()
        with urllib.request.urlopen("http://127.0.0.1:%s/api/zyje" % port, timeout=2):
            otworz_okno("http://127.0.0.1:%s/#%s" % (port, klucz))
            return
    except Exception:
        pass

    try:
        app = Aplikacja()
    except Exception as e:
        LOG.exception("Start nie wyszedl")
        _komunikat("Nie udało się uruchomić aplikacji:\n\n%s\n\nSzczegóły w pliku radar.log." % e)
        return
    Obsluga.app = app
    Obsluga.klucz = secrets.token_urlsafe(24)
    serwer = ThreadingHTTPServer(("127.0.0.1", 0), Obsluga)
    serwer.daemon_threads = True
    port = serwer.server_address[1]
    try:
        with open(PLIK_PORTU, "w", encoding="utf-8") as f:
            f.write("%d %s" % (port, Obsluga.klucz))
    except OSError:
        pass
    LOG.info("Radar Karier dziala na http://127.0.0.1:%d", port)

    def straznik():
        # okno wysyla sygnal co kilka sekund; gdy zamkniesz okno, konczymy aplikacje
        while True:
            time.sleep(5)
            teraz = time.time()
            if app.watek and app.watek.is_alive():
                continue                           # pobieranie ofert trwa - czekamy
            zamkniete = (app.okno_zamkniete and app.ostatni_sygnal <= app.okno_zamkniete
                         and teraz - app.okno_zamkniete > PO_ZAMKNIECIU_S)
            if zamkniete or teraz - app.ostatni_sygnal > BEZ_SYGNALU_S:
                LOG.info("Okno zamkniete - koncze")
                app.zatrzymaj_wifi()
                serwer.shutdown()
                return
    app.ostatni_sygnal = time.time() + 30          # czas na otwarcie okna
    if baza.meta_get(app.conn, "wifi"):            # dostep z telefonu byl wlaczony
        app.uruchom_wifi()
    threading.Thread(target=straznik, daemon=True).start()
    otworz_okno("http://127.0.0.1:%d/#%s" % (port, Obsluga.klucz))
    try:
        serwer.serve_forever()
    finally:
        try:
            os.remove(PLIK_PORTU)
        except OSError:
            pass
        try:
            app.conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
