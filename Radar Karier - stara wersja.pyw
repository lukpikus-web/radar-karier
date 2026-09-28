# -*- coding: utf-8 -*-
"""
Radar Karier - aplikacja okienkowa.

Uruchamianie: kliknij dwa razy ten plik (albo start.bat).
Dane: radar.db w tym samym folderze. Kopie zapasowe: folder "kopie".
Dziennik bledow: radar.log.
"""

import hashlib
import json
import math
import logging
import os
import queue
import sys
import threading
import time
import traceback
import urllib.error
import webbrowser
from datetime import datetime, timedelta, timezone

KATALOG = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, KATALOG)

# --- dziennik (okno nie ma konsoli, wiec bledy laduja w pliku) --------------
PLIK_LOGU = os.path.join(KATALOG, "radar.log")
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

try:   # ostry obraz na ekranach z powiekszeniem (Windows)
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk

import baza
import dopasowanie
import radar

PLIK_BAZY = os.path.join(KATALOG, "radar.db")
PLIK_JSON = os.path.join(KATALOG, "oferty.json")
KATALOG_KOPII = os.path.join(KATALOG, "kopie")

STATUSY = [
    ("nowa",         "Nowa"),
    ("do_zrobienia", "Do aplikowania"),
    ("zaaplikowana", "Zaaplikowana"),
    ("odrzucona",    "Odrzucona"),
    ("nieciekawa",   "Nieciekawa"),
]
NAZWA_STATUSU = dict(STATUSY)

WIDOKI = [
    ("warszawa", "Tylko Warszawa"),
    ("zdalne",   "Tylko zdalne"),
    ("ukryj",    "Ukryj wygasłe"),
    ("nowe",     "Tylko nowe"),
    ("dopasowane", "Dopasowane do CV (50%+)"),     # widoczne, gdy jest CV
]
SORTOWANIE = [("data", "Najnowsze"), ("cv", "Najlepiej dopasowane do CV")]
PROG_DOPASOWANIA = 50

NA_STRONE = 10         # tyle ofert na jednej stronie listy
MAX_SZEROKOSC = 1100   # szerokosc kolumny z kartami (jak na stronie www)

KOLORY = {
    "tlo": "#f5f6f8", "karta": "#ffffff", "tekst": "#111827", "slaby": "#6b7280",
    "linia": "#e5e7eb", "linia_hover": "#c7d2fe",
    "akcent": "#2563eb", "akcent_ciemny": "#1d4ed8", "akcent_tlo": "#eff6ff",
    "pole": "#f3f4f6", "wylaczony": "#93b4f5",
}

# pasek z lewej strony karty
KOLOR_STATUSU = {
    "nowa": "#2563eb", "do_zrobienia": "#d97706", "zaaplikowana": "#15803d",
    "odrzucona": "#9ca3af", "nieciekawa": "#d1d5db",
}

# (tekst, tlo, ramka) wcisnietego przycisku statusu
AKTYWNY_STATUS = {
    "nowa":         ("#111827", "#ffffff", "#9ca3af"),
    "do_zrobienia": ("#92400e", "#fef3c7", "#fcd34d"),
    "zaaplikowana": ("#15803d", "#dcfce7", "#86efac"),
    "odrzucona":    ("#374151", "#e5e7eb", "#9ca3af"),
    "nieciekawa":   ("#374151", "#e5e7eb", "#9ca3af"),
}

# (tekst, tlo) malych etykiet na karcie
ETYKIETY = {
    "nowa":    ("#1d4ed8", "#eff6ff"),
    "zdalnie": ("#15803d", "#ecfdf3"),
    "kasa":    ("#92400e", "#fef3c7"),
    "wygasla": ("#b91c1c", "#fef2f2"),
    "zwykla":  ("#4b5563", "#f3f4f6"),
    "dop_wys": ("#ffffff", "#15803d"),     # dopasowanie do CV 70%+
    "dop_sr":  ("#92400e", "#fde68a"),     # 45-69%
    "dop_nis": ("#4b5563", "#e5e7eb"),     # ponizej 45%
}


def dni_temu(data):
    if not data:
        return ""
    try:
        d = datetime.strptime(data[:10], "%Y-%m-%d").date()
    except ValueError:
        return data[:10]
    dni = (datetime.now().date() - d).days
    if dni <= 0:
        return "dziś"
    if dni == 1:
        return "wczoraj"
    if dni < 31:
        return "%d dni temu" % dni
    return d.strftime("%d.%m.%Y")


def lokalny_czas(iso):
    if not iso:
        return "nigdy"
    try:
        d = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return d.astimezone().strftime("%d.%m, %H:%M")
    except ValueError:
        return iso


def czy_nowa(o):
    p = o.get("pierwszy_raz")
    if not p:
        return False
    try:
        d = datetime.strptime(p, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - d < timedelta(days=3)


def skroc(tekst, dlugosc):
    tekst = " ".join((tekst or "").split())
    return tekst if len(tekst) <= dlugosc else tekst[:dlugosc - 1].rstrip() + "…"


def data_oferty(o):
    return o.get("opublikowano") or (o.get("pierwszy_raz") or "")[:10]


class PoleZPodpowiedzia(tk.Entry):
    """Pole tekstowe z szarym napisem-podpowiedzia, gdy jest puste."""

    def __init__(self, master, podpowiedz, **kw):
        super().__init__(master, **kw)
        self.podpowiedz = podpowiedz
        self.kolor = kw.get("fg", KOLORY["tekst"])
        self.pusto = False
        self.bind("<FocusIn>", self._wejscie, add="+")
        self.bind("<FocusOut>", self._wyjscie, add="+")
        self._wyjscie()

    def _wejscie(self, _e=None):
        if self.pusto:
            self.delete(0, "end")
            self.configure(fg=self.kolor)
            self.pusto = False

    def _wyjscie(self, _e=None):
        if not super().get():
            self.pusto = True
            self.insert(0, self.podpowiedz)
            self.configure(fg=KOLORY["slaby"])

    def wartosc(self):
        return "" if self.pusto else super().get()

    def ustaw(self, tekst):
        self.delete(0, "end")
        self.pusto = False
        self.configure(fg=self.kolor)
        if tekst:
            self.insert(0, tekst)
        elif self.focus_get() is not self:
            self._wyjscie()


class Aplikacja:

    def __init__(self, root):
        self.root = root
        self.kolejka = queue.Queue()
        self.przerwij = threading.Event()
        self.watek = None
        self.oferty = {}          # id -> slownik
        self.widoczne = []        # oferty po filtrach, w kolejnosci
        self.strona_nr = 0        # ktora strona listy (od 0)
        self.karty = {}           # id -> elementy karty na plotnie
        self.timer_notatki = None
        self.timer_szukania = None
        self.timer_ukladu = None
        self.menu_id = None
        self.podswietlona = None
        self.suma_kolka = 0.0
        self.do_przewiniecia = 0      # piksele zebrane z kolka/touchpada, czekajace na klatke
        self.timer_przewijania = None
        self.ostatnie_przewiniecie = 0.0
        self.naglowek_ukryty = False
        self.szerokosc = 0        # szerokosc kolumny z kartami
        self.x0 = 24              # jej lewa krawedz
        self.y_listy = 0          # gdzie zaczyna sie lista kart

        # szczegoly ofert (obowiazki, wymagania) - pobierane w tle na zadanie
        self.szczegoly = {}       # id -> {"obowiazki": [...], ...} (z bazy)
        self.rozwiniete = set()   # karty z rozwinietymi szczegolami
        self.pobierane = set()    # w trakcie pobierania
        self.bledy_szczeg = {}    # id -> opis bledu
        self.kolejka_szczeg = queue.Queue()
        self.watek_szczeg = None
        self.do_przerysowania = False

        # filtry: pusty zbior = wszystko
        self.f_zrodla, self.f_kategorie, self.f_statusy = set(), set(), set()
        self.f_widok = {"ukryj"}
        self.sortowanie = "data"
        self.chipy = {}           # (grupa, wartosc) -> etykieta
        self.cv = None            # {"tekst", "umiejetnosci", "plik", "zapisano"} albo None
        self.profil = None        # dopasowanie.Profil
        self.dopasowania = {}     # id -> {"wynik", "pasuje", "brakuje"}

        self._baza_start()
        self._wczytaj_cv()
        self._wczytaj_filtry()
        self._styl()
        self._zbuduj()
        self.przeladuj()
        self.root.after(150, self._obsluz_kolejke)
        # bez odswiezania przy starcie - oferty pobiera sie recznie (przycisk albo F5)
        self.root.protocol("WM_DELETE_WINDOW", self.zamknij)

    # ------------------------------------------------------------------ baza
    def _baza_start(self):
        self.conn = baza.otworz(PLIK_BAZY)
        if (baza.liczba_ofert(self.conn) == 0 and os.path.exists(PLIK_JSON)
                and not baza.meta_get(self.conn, "bez_importu_json")):
            ile = baza.importuj_json(self.conn, PLIK_JSON)
            LOG.info("Zaimportowano %d ofert z oferty.json", ile)
        # ustawienia wyszukiwania (frazy, filtry, miasto) - z bazy albo domyslne
        zapisane = baza.meta_get(self.conn, "ustawienia")
        try:
            zapisane = json.loads(zapisane) if zapisane else None
        except ValueError:
            LOG.warning("Uszkodzone ustawienia w bazie - biorę domyślne")
            zapisane = None
        self.ustawienia = radar.zastosuj_ustawienia(zapisane)
        try:
            baza.kopia_zapasowa(self.conn, KATALOG_KOPII)
        except Exception:
            LOG.exception("Kopia zapasowa nie wyszla")

    # ------------------------------------------------------------------ wyglad
    def _styl(self):
        r = self.root
        r.title("Radar Karier")
        r.geometry("1200x860")
        r.minsize(760, 560)
        r.configure(bg=KOLORY["tlo"])

        rodzina = "Segoe UI" if sys.platform.startswith("win") else "DejaVu Sans"
        for nazwa in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(nazwa).configure(family=rodzina, size=10)
            except tk.TclError:
                pass
        F = lambda size, **kw: tkfont.Font(family=rodzina, size=size, **kw)
        self.f_tytul = F(18, weight="bold")
        self.f_duzy = F(15, weight="bold")
        self.f_oferta = F(12, weight="bold")
        self.f_oferta_hover = F(12, weight="bold", underline=True)
        self.f_zwykly = F(10)
        self.f_maly = F(9)
        self.f_etykieta = F(8)
        self.f_etykieta_b = F(8, weight="bold")
        self.f_pigulka = F(9)
        self.f_pigulka_b = F(9, weight="bold")
        self.f_link = F(9, weight="bold")
        self.f_link_hover = F(9, weight="bold", underline=True)
        self.f_etykieta_b_hover = F(8, weight="bold", underline=True)
        self.f_sekcja = F(9, weight="bold")
        self.f_przycisk = F(10, weight="bold")
        self.f_szukaj = F(11)

        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Radar.Horizontal.TProgressbar", troughcolor=KOLORY["linia"],
                    background=KOLORY["akcent"], bordercolor=KOLORY["tlo"],
                    lightcolor=KOLORY["akcent"], darkcolor=KOLORY["akcent"], thickness=6)
        s.configure("Radar.Vertical.TScrollbar", troughcolor=KOLORY["tlo"],
                    background="#d1d5db", bordercolor=KOLORY["tlo"], arrowcolor=KOLORY["slaby"],
                    lightcolor="#d1d5db", darkcolor="#d1d5db")

    # --- male klocki ------------------------------------------------------
    def _ramka(self, master, bg=None, **kw):
        return tk.Frame(master, bg=bg or KOLORY["karta"], **kw)

    def _napis(self, master, text="", bg=None, fg=None, font=None, **kw):
        return tk.Label(master, text=text, bg=bg or KOLORY["karta"], fg=fg or KOLORY["tekst"],
                        font=font or self.f_zwykly, **kw)

    def _pigulka(self, master, tekst, komenda, font=None):
        p = tk.Label(master, text=tekst, font=font or self.f_pigulka, bg=KOLORY["karta"],
                     fg=KOLORY["tekst"], padx=11, pady=4, cursor="hand2",
                     highlightthickness=1, highlightbackground=KOLORY["linia"], bd=0)
        p.bind("<Button-1>", lambda _e: komenda())
        return p

    def _zbuduj(self):
        r = self.root

        # stopka na samym dole (poza przewijaniem)
        stopka = tk.Frame(r, bg=KOLORY["tlo"])
        stopka.pack(side="bottom", fill="x")
        tk.Frame(stopka, bg=KOLORY["linia"], height=1).pack(fill="x")
        self.l_stopka = tk.Label(stopka, text="", bg=KOLORY["tlo"], fg=KOLORY["slaby"],
                                 font=self.f_maly, anchor="w", padx=16, pady=5)
        self.l_stopka.pack(fill="x")

        # przewijana "strona". Karty sa RYSOWANE na plotnie (a nie skladane
        # z setek widzetow) - dzieki temu przewijanie jest plynne.
        obszar = tk.Frame(r, bg=KOLORY["tlo"])
        obszar.pack(fill="both", expand=True)
        self.plotno = tk.Canvas(obszar, bg=KOLORY["tlo"], highlightthickness=0, bd=0,
                                yscrollincrement=1)          # przewijanie co piksel
        pas = ttk.Scrollbar(obszar, orient="vertical", command=self._przewin_pasek,
                            style="Radar.Vertical.TScrollbar")
        self.pas = pas
        self.plotno.configure(yscrollcommand=self._po_przewinieciu)
        pas.pack(side="right", fill="y")
        self.plotno.pack(side="left", fill="both", expand=True)
        self.strona = tk.Frame(self.plotno, bg=KOLORY["tlo"])
        self.okno_strony = self.plotno.create_window(0, 0, window=self.strona, anchor="nw")
        self.strona.bind("<Configure>", lambda _e: self._rozloz_za_chwile())
        self.plotno.bind("<Configure>", self._dopasuj_szerokosc)

        # kolko myszy i touchpad - w calym oknie
        r.bind_all("<MouseWheel>", self._kolko)
        r.bind_all("<Shift-MouseWheel>", lambda _e: None)
        r.bind_all("<Button-4>", lambda _e: self._przewin_o(-60))
        r.bind_all("<Button-5>", lambda _e: self._przewin_o(60))
        try:    # Tk 9 (nowsze Pythony): gesty touchpada to osobne zdarzenie
            r.bind_all("<TouchpadScroll>", self._touchpad)
        except tk.TclError:
            pass
        r.bind_all("<Prior>", lambda _e: self._strona_klawisz(-1))
        r.bind_all("<Next>", lambda _e: self._strona_klawisz(1))
        r.bind_all("<Home>", lambda _e: self._klawisz_skok(0.0))
        r.bind_all("<End>", lambda _e: self._klawisz_skok(1.0))
        r.bind_all("<Control-Right>", lambda _e: self._strona_obok(1))
        r.bind_all("<Control-Left>", lambda _e: self._strona_obok(-1))

        self._naglowek(self.strona)
        self._panel_filtrow(self.strona)

        # edytor notatki: JEDNO pole, pokazywane nad kliknieta notatka
        self.edytor = tk.Entry(self.plotno, font=self.f_maly, bg="white", fg=KOLORY["tekst"],
                               relief="flat", highlightthickness=1,
                               highlightbackground=KOLORY["akcent"],
                               highlightcolor=KOLORY["akcent"], insertbackground=KOLORY["tekst"])
        self.edytor.bind("<Return>", lambda _e: self._koniec_edycji(True))
        self.edytor.bind("<Escape>", lambda _e: (self._koniec_edycji(False), "break")[1])
        self.edytor.bind("<FocusOut>", lambda _e: self._koniec_edycji(True))
        self.edytor.bind("<KeyRelease>", lambda _e: self._notatka_pisze())
        self.okno_edytora = None
        self.edytowana = None

        self._podepnij_plotno()

        self.menu = tk.Menu(r, tearoff=0)
        self.menu.add_command(label="Otwórz ofertę w przeglądarce",
                              command=lambda: self.otworz_oferte(self.menu_id))
        self.menu.add_command(label="Kopiuj link", command=lambda: self.kopiuj_link(self.menu_id))
        self.menu.add_command(label="Pokaż / zwiń obowiązki i wymagania",
                              command=lambda: self.przelacz_szczegoly(self.menu_id))

        r.bind("<F5>", lambda _e: self.odswiez())
        r.bind("<Control-f>", lambda _e: self._do_szukania())
        r.bind("<Escape>", lambda _e: self._wyczysc_szukanie())

    def _naglowek(self, master):
        gora = tk.Frame(master, bg=KOLORY["tlo"])
        gora.pack(fill="x", pady=(22, 0))
        lewo = tk.Frame(gora, bg=KOLORY["tlo"])
        lewo.pack(side="left")
        self._napis(lewo, "Radar Karier", bg=KOLORY["tlo"], font=self.f_tytul).pack(anchor="w")
        self.l_podtytul = self._napis(lewo, "", bg=KOLORY["tlo"], fg=KOLORY["slaby"],
                                      font=self.f_maly)
        self.l_podtytul.pack(anchor="w", pady=(2, 0))

        prawo = tk.Frame(gora, bg=KOLORY["tlo"])
        prawo.pack(side="right")
        self.b_odswiez = tk.Label(prawo, text="Odśwież oferty", font=self.f_przycisk,
                                  bg=KOLORY["akcent"], fg="white", padx=16, pady=8, cursor="hand2")
        self.b_odswiez.pack(side="right")
        self.b_odswiez.bind("<Button-1>", lambda _e: self.odswiez())
        self.b_odswiez.bind("<Enter>", lambda _e: self._przycisk_hover(True))
        self.b_odswiez.bind("<Leave>", lambda _e: self._przycisk_hover(False))
        self.b_ustawienia = tk.Label(prawo, text="⚙  Ustawienia", font=self.f_przycisk,
                                     bg=KOLORY["karta"], fg=KOLORY["tekst"], padx=14, pady=7,
                                     cursor="hand2", highlightthickness=1,
                                     highlightbackground=KOLORY["linia"])
        self.b_ustawienia.pack(side="right", padx=(0, 8))
        self.b_ustawienia.bind("<Button-1>", lambda _e: self.otworz_ustawienia())
        self.b_ustawienia.bind("<Enter>", lambda _e: self.b_ustawienia.configure(bg=KOLORY["pole"]))
        self.b_ustawienia.bind("<Leave>", lambda _e: self.b_ustawienia.configure(bg=KOLORY["karta"]))
        self.b_cv = tk.Label(prawo, text="Moje CV", font=self.f_przycisk,
                             bg=KOLORY["karta"], fg=KOLORY["tekst"], padx=14, pady=7,
                             cursor="hand2", highlightthickness=1,
                             highlightbackground=KOLORY["linia"])
        self.b_cv.pack(side="right", padx=(0, 8))
        self.b_cv.bind("<Button-1>", lambda _e: self.otworz_cv())
        self.b_cv.bind("<Enter>", lambda _e: self.b_cv.configure(bg=KOLORY["pole"]))
        self.b_cv.bind("<Leave>", lambda _e: self.b_cv.configure(bg=KOLORY["karta"]))
        self._opis_przycisku_cv()
        self.pasek = ttk.Progressbar(prawo, mode="indeterminate", length=120,
                                     style="Radar.Horizontal.TProgressbar")

        liczniki = tk.Frame(master, bg=KOLORY["tlo"])
        liczniki.pack(fill="x", pady=(16, 16))
        self.liczniki = {}
        for klucz, nazwa in [("aktywne", "Aktywne oferty"), ("nowe", "Nowe (3 dni)"),
                             ("do_zrobienia", "Do aplikowania"), ("zaaplikowana", "Zaaplikowane"),
                             ("odrzucona", "Odrzucone")]:
            k = tk.Frame(liczniki, bg=KOLORY["karta"], highlightthickness=1,
                         highlightbackground=KOLORY["linia"], padx=14, pady=10)
            k.pack(side="left", padx=(0, 8))
            w = self._napis(k, "0", font=self.f_duzy)
            w.pack(anchor="w")
            n = self._napis(k, nazwa, fg=KOLORY["slaby"], font=self.f_maly)
            n.pack(anchor="w", pady=(2, 0))
            self.liczniki[klucz] = w
            if klucz in NAZWA_STATUSU or klucz == "nowe":   # klik = szybki filtr
                for x in (k, w, n):
                    x.configure(cursor="hand2")
                    x.bind("<Button-1>", lambda _e, kk=klucz: self._szybki_filtr(kk))

    def _panel_filtrow(self, master):
        panel = tk.Frame(master, bg=KOLORY["karta"], highlightthickness=1,
                         highlightbackground=KOLORY["linia"], padx=14, pady=14)
        panel.pack(fill="x")
        self.e_szukaj = PoleZPodpowiedzia(
            panel, "Szukaj w tytule, firmie, opisie…", font=self.f_szukaj, bg=KOLORY["pole"],
            fg=KOLORY["tekst"], relief="flat", highlightthickness=1,
            highlightbackground=KOLORY["linia"], highlightcolor=KOLORY["akcent"],
            insertbackground=KOLORY["tekst"])
        self.e_szukaj.pack(fill="x", ipady=8, ipadx=8)
        self.e_szukaj.bind("<KeyRelease>", lambda _e: self._szukaj_za_chwile())

        self.wiersze_filtrow = {}
        rzedy = {}
        for grupa, nazwa in [("zrodlo", "Źródło"), ("kategoria", "Kategoria"),
                             ("status", "Status"), ("widok", "Widok"), ("sort", "Sortuj")]:
            tk.Frame(panel, bg=KOLORY["linia"], height=1).pack(fill="x", pady=(10, 8))
            w = tk.Frame(panel, bg=KOLORY["karta"])
            w.pack(fill="x")
            rzedy[grupa] = w
            self._napis(w, nazwa, fg=KOLORY["slaby"], font=self.f_maly, width=11,
                        anchor="w").pack(side="left")
            chipy = tk.Frame(w, bg=KOLORY["karta"])
            chipy.pack(side="left", fill="x")
            self.wiersze_filtrow[grupa] = chipy
        # bez CV: podpowiedz przy sortowaniu
        self.l_bez_cv = self._napis(rzedy["sort"], "Dodaj CV (przycisk „Moje CV”), żeby zobaczyć "
                                    "dopasowanie ofert", fg=KOLORY["slaby"], font=self.f_maly)
        # filtry sa zapamietywane - latwy powrot do pelnej listy
        w = rzedy["widok"]
        self.b_wyczysc = self._napis(w, "✕  Wyczyść filtry", fg=KOLORY["akcent"], font=self.f_link,
                                     cursor="hand2")
        self.b_wyczysc.bind("<Button-1>", lambda _e: self.wyczysc_filtry())
        self.b_wyczysc.bind("<Enter>", lambda _e: self.b_wyczysc.configure(font=self.f_link_hover))
        self.b_wyczysc.bind("<Leave>", lambda _e: self.b_wyczysc.configure(font=self.f_link))
        self._chipy("status", STATUSY)
        self._chipy("widok", self._widoki())
        self._chipy("sort", SORTOWANIE)

    def _widoki(self):
        # "Tylko Warszawa" -> "Tylko <miasto z ustawien>"; filtr CV tylko, gdy jest CV
        return [(k, "Tylko " + radar.MIASTO_NAZWA if k == "warszawa" else n) for k, n in WIDOKI
                if (k != "dopasowane" or self.cv) and (k != "warszawa" or radar.MIASTO_NAZWA)]

    def _chipy(self, grupa, pozycje):
        ramka = self.wiersze_filtrow[grupa]
        for w in ramka.winfo_children():
            w.destroy()
        for k in [k for k in self.chipy if k[0] == grupa]:
            del self.chipy[k]
        for wartosc, tekst in pozycje:
            p = self._pigulka(ramka, tekst, lambda g=grupa, v=wartosc: self._przelacz(g, v))
            p.pack(side="left", padx=(0, 6))
            self.chipy[(grupa, wartosc)] = p
        self._odswiez_chipy()

    def _zbior(self, grupa):
        return {"zrodlo": self.f_zrodla, "kategoria": self.f_kategorie,
                "status": self.f_statusy, "widok": self.f_widok,
                "sort": {self.sortowanie}}[grupa]

    def _odswiez_chipy(self):
        if hasattr(self, "l_bez_cv"):
            if self.cv:
                self.l_bez_cv.pack_forget()
            elif not self.l_bez_cv.winfo_ismapped():
                self.l_bez_cv.pack(side="left", padx=(6, 0))
        if hasattr(self, "b_wyczysc"):
            if self._filtry_domyslne():
                self.b_wyczysc.pack_forget()
            elif not self.b_wyczysc.winfo_ismapped():
                self.b_wyczysc.pack(side="right")
        for (grupa, wartosc), p in self.chipy.items():
            wlaczony = wartosc in self._zbior(grupa)
            p.configure(bg=KOLORY["akcent_tlo"] if wlaczony else KOLORY["karta"],
                        fg=KOLORY["akcent_ciemny"] if wlaczony else KOLORY["tekst"],
                        highlightbackground=KOLORY["akcent"] if wlaczony else KOLORY["linia"],
                        font=self.f_pigulka_b if wlaczony else self.f_pigulka)

    def _przelacz(self, grupa, wartosc):
        if grupa == "sort":                    # sortowanie: zawsze jedno
            if wartosc == "cv" and not self.cv:
                messagebox.showinfo("Najpierw CV", "Żeby sortować po dopasowaniu, dodaj swoje CV.")
                self.otworz_cv()
                return
            self.sortowanie = wartosc
            self._zapisz_filtry()
            self._odswiez_chipy()
            self.rysuj()
            return
        z = self._zbior(grupa)
        z.symmetric_difference_update({wartosc})
        self._zapisz_filtry()
        self._odswiez_chipy()
        self.rysuj()

    # --- zapamietywanie filtrow (w radar.db, do czasu zmiany) ----------------
    DOMYSLNY_WIDOK = {"ukryj"}

    def _filtry_domyslne(self):
        return not (self.f_zrodla or self.f_kategorie or self.f_statusy) and \
            self.f_widok == self.DOMYSLNY_WIDOK

    def _wczytaj_filtry(self):
        try:
            f = json.loads(baza.meta_get(self.conn, "filtry") or "null")
        except ValueError:
            f = None
        if not isinstance(f, dict):
            return
        def lista(klucz, dozwolone=None):
            v = f.get(klucz)
            if not isinstance(v, list):
                return None
            return {x for x in v if isinstance(x, str) and (dozwolone is None or x in dozwolone)}
        self.f_zrodla = lista("zrodla") or set()
        self.f_kategorie = lista("kategorie") or set()
        self.f_statusy = lista("statusy", NAZWA_STATUSU) or set()
        widok = lista("widok", {k for k, _ in WIDOKI})
        if widok is not None:
            self.f_widok = widok
        if f.get("sort") in dict(SORTOWANIE):
            self.sortowanie = f["sort"]

    def _zapisz_filtry(self):
        try:
            baza.meta_set(self.conn, "filtry", json.dumps({
                "zrodla": sorted(self.f_zrodla), "kategorie": sorted(self.f_kategorie),
                "statusy": sorted(self.f_statusy), "widok": sorted(self.f_widok),
                "sort": self.sortowanie},
                ensure_ascii=False))
        except Exception:
            LOG.exception("Zapis filtrow nie wyszedl")

    def wyczysc_filtry(self):
        self.f_zrodla.clear()
        self.f_kategorie.clear()
        self.f_statusy.clear()
        self.f_widok = set(self.DOMYSLNY_WIDOK)
        self._zapisz_filtry()
        self._odswiez_chipy()
        self.rysuj()

    def _szybki_filtr(self, klucz):
        if klucz == "nowe":
            self.f_widok.symmetric_difference_update({"nowe"})
        elif self.f_statusy == {klucz}:     # drugi klik wylacza filtr
            self.f_statusy.clear()
        else:
            self.f_statusy.clear()
            self.f_statusy.add(klucz)
        self._zapisz_filtry()
        self._odswiez_chipy()
        self.rysuj()

    def _przycisk_hover(self, wszedl):
        if self.watek and self.watek.is_alive():
            return
        self.b_odswiez.configure(bg=KOLORY["akcent_ciemny"] if wszedl else KOLORY["akcent"])

    def _wyczysc_szukanie(self):
        if self.e_szukaj.wartosc():
            self.e_szukaj.ustaw("")
            self.rysuj()

    def _szukaj_za_chwile(self):
        if self.timer_szukania:
            self.root.after_cancel(self.timer_szukania)
        self.timer_szukania = self.root.after(250, self.rysuj)

    # ------------------------------------------------------------------ dane
    def przeladuj(self):
        self.oferty = {o["id"]: o for o in baza.wszystkie(self.conn)}
        self.szczegoly = baza.wszystkie_szczegoly(self.conn)
        self._przelicz_dopasowania()
        zrodla = sorted({o["zrodlo"] for o in self.oferty.values() if o["zrodlo"]})
        kategorie = sorted({o["kategoria"] for o in self.oferty.values() if o["kategoria"]})
        self.f_zrodla &= set(zrodla)
        self.f_kategorie &= set(kategorie)
        self._chipy("zrodlo", [(z, z) for z in zrodla])
        self._chipy("kategoria", [(k, k) for k in kategorie])
        t = baza.meta_get(self.conn, "ostatnie_odswiezenie")
        self.l_podtytul.configure(text="%d ofert w bazie  ·  ostatnie odświeżenie: %s"
                                  % (len(self.oferty), lokalny_czas(t)))
        self.rysuj()

    def _filtruj(self):
        q = self.e_szukaj.wartosc().strip().lower()
        wynik = []
        for o in self.oferty.values():
            status = o["status"] or "nowa"
            moja = status != "nowa"
            if "ukryj" in self.f_widok and not o["aktywna"] and not moja:
                continue
            if "warszawa" in self.f_widok and radar.MIASTO_NAZWA and not o["warszawa"]:
                continue
            if "zdalne" in self.f_widok and not o["zdalna"]:
                continue
            if "nowe" in self.f_widok and not czy_nowa(o):
                continue
            if "dopasowane" in self.f_widok and self.cv and \
                    self.dopasowania.get(o["id"], {}).get("wynik", 0) < PROG_DOPASOWANIA:
                continue
            if self.f_zrodla and o["zrodlo"] not in self.f_zrodla:
                continue
            if self.f_kategorie and o["kategoria"] not in self.f_kategorie:
                continue
            if self.f_statusy and status not in self.f_statusy:
                continue
            if q:
                stog = " ".join(str(o.get(p) or "") for p in
                                ("tytul", "firma", "opis", "lokalizacja")).lower()
                if q not in stog:
                    continue
            wynik.append(o)
        wynik.sort(key=data_oferty, reverse=True)
        if self.sortowanie == "cv" and self.cv:          # stabilnie: przy remisie nowsze wyzej
            wynik.sort(key=lambda o: self.dopasowania.get(o["id"], {}).get("wynik", 0), reverse=True)
        return wynik

    # ------------------------------------------------------------------ przewijanie
    def _przewin_o(self, piksele):
        # touchpad i kolko wysylaja dziesiatki zdarzen na sekunde - zbieramy je
        # i przewijamy raz na klatke, zamiast przerysowywac przy kazdym
        if not piksele:
            return
        self.do_przewiniecia += int(piksele)
        if self.timer_przewijania is None:
            self.timer_przewijania = self.root.after(16, self._przewin_teraz)   # ~60 klatek/s

    def _przewin_teraz(self):
        self.timer_przewijania = None
        krok, self.do_przewiniecia = self.do_przewiniecia, 0
        if krok:
            self.plotno.yview_scroll(krok, "units")

    def _po_przewinieciu(self, gora, dol):
        """Wolane przy kazdej zmianie widoku (kolko, pasek, klawisze, strony)."""
        self.pas.set(gora, dol)
        self.ostatnie_przewiniecie = time.time()
        # panel filtrow to kilkadziesiat okienek - gdy jest poza ekranem, chowamy go,
        # zeby Windows nie musial ich przesuwac przy kazdym kroku przewijania
        try:
            wys = float(self.plotno.cget("scrollregion").split()[3])
        except (IndexError, ValueError):
            return
        ukryj = float(gora) * wys > self.strona.winfo_height() + 40
        if ukryj != self.naglowek_ukryty:
            self.naglowek_ukryty = ukryj
            self.plotno.itemconfigure(self.okno_strony, state="hidden" if ukryj else "normal")

    def _przewija(self):
        return time.time() - self.ostatnie_przewiniecie < 0.15

    def _kolko(self, e):
        # mysz: 120 na "zabek"; touchpad na Windows: wiele malych wartosci
        if isinstance(e.widget, str) or e.widget.winfo_toplevel() is not self.root:
            return
        d = e.delta
        if sys.platform == "darwin":
            d *= 30
        self.suma_kolka += -d * 0.6          # 120 -> ok. 70 pikseli
        krok = int(self.suma_kolka)
        self.suma_kolka -= krok
        self._przewin_o(krok)

    def _touchpad(self, e):
        # Tk 9: %D zawiera dx i dy (po 16 bitow, ze znakiem)
        try:
            d = int(e.delta)
        except (TypeError, ValueError):
            return
        dy = d & 0xFFFF
        if dy >= 0x8000:
            dy -= 0x10000
        self._przewin_o(-dy)

    def _przewin_pasek(self, *args):
        self.plotno.yview(*args)

    def _pisze(self):
        """True, gdy klawisze naleza do pola tekstowego albo innego okna."""
        w = self.root.focus_get()
        if w is None:
            return False
        return isinstance(w, (tk.Entry, tk.Text, ttk.Entry)) or w.winfo_toplevel() is not self.root

    def _strona_klawisz(self, kierunek):
        if self._pisze():
            return
        self._przewin_o(kierunek * int(self.plotno.winfo_height() * 0.85))

    def _klawisz_skok(self, gdzie):
        if self._pisze():
            return
        self.plotno.yview_moveto(gdzie)

    def _strona_obok(self, kierunek):
        if self._pisze():
            return
        self.idz_do_strony(self.strona_nr + kierunek)

    def _do_szukania(self):
        self.plotno.yview_moveto(0)
        self.e_szukaj.focus_set()
        return "break"

    def _dopasuj_szerokosc(self, e):
        szer = min(MAX_SZEROKOSC, max(400, e.width - 48))
        self.x0 = max(24, (e.width - szer) // 2)
        self.plotno.coords(self.okno_strony, self.x0, 0)
        if szer != self.szerokosc:
            self.szerokosc = szer
            self.plotno.itemconfigure(self.okno_strony, width=szer)
            self._rozloz_za_chwile()

    def _rozloz_za_chwile(self):
        if self.timer_ukladu:
            self.root.after_cancel(self.timer_ukladu)
        self.timer_ukladu = self.root.after(60, self._rozloz)

    def _rozloz(self):
        """Rysuje od nowa karty biezacej strony."""
        self.timer_ukladu = None
        self._koniec_edycji(True)
        c = self.plotno
        c.delete("karta", "pager", "pusto")
        self.karty.clear()
        self.podswietlona = None
        c.update_idletasks()
        y = self.strona.winfo_reqheight() + 14
        self.y_listy = y

        stron = self.liczba_stron()
        self.strona_nr = max(0, min(self.strona_nr, stron - 1))
        od = self.strona_nr * NA_STRONE
        do = min(od + NA_STRONE, len(self.widoczne))

        if not self.widoczne:
            c.create_text(self.x0 + self.szerokosc // 2, y + 30, text="Brak ofert dla wybranych filtrów.",
                          fill=KOLORY["slaby"], font=self.f_zwykly, tags=("pusto",))
            y += 60
        else:
            y = self._pager(y) + 12
            for nr in range(od, do):
                y = self._karta(nr, self.widoczne[nr], y) + 10
            y = self._pager(y + 4)
        c.configure(scrollregion=(0, 0, self.x0 * 2 + self.szerokosc, y + 24))

    def liczba_stron(self):
        return max(1, -(-len(self.widoczne) // NA_STRONE))

    def idz_do_strony(self, nr):
        nr = max(0, min(nr, self.liczba_stron() - 1))
        if nr == self.strona_nr:
            return
        self.strona_nr = nr
        self._rozloz()
        # przewin tak, zeby pierwsza oferta nowej strony byla na gorze okna
        wys = float(self.plotno.cget("scrollregion").split()[3])
        self.plotno.yview_moveto(max(0.0, (self.y_listy - 8) / wys))
        self._stopka_domyslna()

    def _pager(self, y):
        """Pasek stron: "‹ Poprzednia  1 … 4 [5] 6 … 34  Następna ›". Zwraca dol."""
        c = self.plotno
        stron = self.liczba_stron()
        od = self.strona_nr * NA_STRONE
        do = min(od + NA_STRONE, len(self.widoczne))
        lewo, prawo = self.x0, self.x0 + self.szerokosc
        info = c.create_text(lewo + 2, y + 15, anchor="w", font=self.f_maly, fill=KOLORY["slaby"],
                             text="Oferty %d–%d z %d" % (od + 1, do, len(self.widoczne)),
                             tags=("pager",))
        if stron == 1:
            return c.bbox(info)[3] + 4

        # numery: pierwsza, ostatnia, biezaca +-2, reszta jako "…"
        pokaz = sorted({0, stron - 1} | set(range(self.strona_nr - 2, self.strona_nr + 3)))
        pozycje = [("prev", "‹ Poprzednia")]
        poprz = -1
        for n in pokaz:
            if 0 <= n < stron:
                if n - poprz > 1:
                    pozycje.append((None, "…"))
                pozycje.append((n, str(n + 1)))
                poprz = n
        pozycje.append(("next", "Następna ›"))

        # od prawej do lewej, zeby pasek byl wyrownany do prawej
        x, wys = prawo, 0
        for cel, tekst in reversed(pozycje):
            biezaca = cel == self.strona_nr
            nieaktywna = (cel == "prev" and self.strona_nr == 0) or \
                         (cel == "next" and self.strona_nr == stron - 1)
            if cel is None:
                t = c.create_text(x - 4, y + 15, text=tekst, anchor="e", font=self.f_pigulka,
                                  fill=KOLORY["slaby"], tags=("pager",))
                x = c.bbox(t)[0] - 6
                continue
            font = self.f_pigulka_b if biezaca else self.f_pigulka
            szer = font.measure(tekst) + 20
            if isinstance(cel, int):
                szer = max(szer, 32)
            tagi = ("pager",) if (biezaca or nieaktywna) else ("pager", "p_%s" % cel, "reka")
            if biezaca:
                fg, bg, ramka = "white", KOLORY["akcent"], KOLORY["akcent"]
            elif nieaktywna:
                fg, bg, ramka = KOLORY["slaby"], KOLORY["tlo"], KOLORY["linia"]
            else:
                fg, bg, ramka = KOLORY["tekst"], KOLORY["karta"], KOLORY["linia"]
            x0 = x - szer
            self._zaokr(x0, y, x, y + 30, 7, fill=bg, outline=ramka, tags=tagi)
            c.create_text((x0 + x) / 2, y + 15, text=tekst, font=font, fill=fg, tags=tagi)
            wys = 30
            x = x0 - 6
        return y + max(wys, 20)

    def _klik_pager(self):
        cel = next((t[2:] for t in self.plotno.gettags("current") if t.startswith("p_")), None)
        if cel == "prev":
            self.idz_do_strony(self.strona_nr - 1)
        elif cel == "next":
            self.idz_do_strony(self.strona_nr + 1)
        elif cel is not None and cel.isdigit():
            self.idz_do_strony(int(cel))

    # ------------------------------------------------------------------ lista kart
    def rysuj(self):
        self.timer_szukania = None
        self._koniec_edycji(True)
        self.widoczne = self._filtruj()
        self.strona_nr = 0
        self._rozloz()
        self.plotno.yview_moveto(0)
        self._liczniki()
        self._stopka_domyslna()

    # --- rysowanie na plotnie -------------------------------------------
    def _zaokr(self, x0, y0, x1, y1, r, **kw):
        # gotowe punkty lukow zamiast smooth=True - Tk nie liczy krzywych przy
        # kazdym przerysowaniu (to spowalnialo przewijanie)
        return self.plotno.create_polygon(self._punkty(x0, y0, x1, y1, r), **kw)

    def _pigulka_na_plotnie(self, x, y, tekst, fg, bg, ramka, font, tagi, padx=10, pady=4, r=7):
        """Zaokraglona etykieta; zwraca (tlo, tekst, szerokosc, wysokosc)."""
        c = self.plotno
        t = c.create_text(x + padx, y + pady, text=tekst, anchor="nw", fill=fg, font=font, tags=tagi)
        _, _, x1, y1 = c.bbox(t)
        tlo = self._zaokr(x, y, x1 + padx, y1 + pady, r, fill=bg, outline=ramka, tags=tagi)
        c.tag_lower(tlo, t)
        return tlo, t, x1 + padx - x, y1 + pady - y

    def _karta(self, nr, o, y):
        """Rysuje karte oferty od wysokosci y; zwraca y jej dolnej krawedzi.

        Klikanie obsluguja wspolne tagi (podpiete raz w _podepnij_plotno);
        ktora to karta, mowi tag "k<nr>" na kliknietym elemencie."""
        c = self.plotno
        ident = o["id"]
        tagi = ("karta", "k%d" % nr)
        otw = tagi + ("otw", "reka")            # klik -> strona oferty
        ma_link = bool(o.get("url"))
        if not ma_link:
            otw = tagi
        x0, x1 = self.x0, self.x0 + self.szerokosc
        lewo, prawo = x0 + 20, x1 - 16

        # tlo (wysokosc poprawiana na koncu). Spod karty w kolorze statusu
        # wystaje 3 px z lewej - to kolorowy pasek.
        pasek = self._zaokr(x0, y, x1, y + 10, 8, fill=KOLOR_STATUSU["nowa"],
                            outline="", tags=tagi)
        ramka = self._zaokr(x0 + 3, y, x1, y + 10, 8, fill=KOLORY["karta"],
                            outline=KOLORY["linia"], tags=tagi)

        # tytul + data
        l_data = c.create_text(prawo, y + 16, text=dni_temu(data_oferty(o)), anchor="ne",
                               fill=KOLORY["slaby"], font=self.f_maly, tags=otw)
        bb = c.bbox(l_data)
        szer_daty = bb[2] - bb[0] if bb else 0
        l_tytul = c.create_text(lewo, y + 14, text=o["tytul"] or "(bez tytułu)", anchor="nw",
                                fill=KOLORY["tekst"], font=self.f_oferta,
                                width=prawo - lewo - szer_daty - 20,
                                tags=otw + (("tytul",) if ma_link else ()))
        yy = c.bbox(l_tytul)[3] + 3

        meta = "  ·  ".join(x for x in (o["firma"], o["lokalizacja"]) if x)
        if meta:
            t = c.create_text(lewo, yy, text=meta, anchor="nw", fill=KOLORY["slaby"],
                              font=self.f_maly, width=prawo - lewo, tags=otw)
            yy = c.bbox(t)[3]

        # etykiety (z zawijaniem)
        yy += 8
        xx, wys = lewo, 0
        for tekst, rodzaj in self._etykiety(o):
            fg, bg = ETYKIETY[rodzaj]
            font = self.f_etykieta_b if rodzaj in ("nowa", "dop_wys", "dop_sr", "dop_nis") \
                else self.f_etykieta
            szer = font.measure(tekst) + 12
            if xx + szer > prawo and xx > lewo:
                xx, yy = lewo, yy + wys + 5
            _, _, sz, wys = self._pigulka_na_plotnie(xx, yy, tekst, fg, bg, bg, font, tagi,
                                                     padx=6, pady=2, r=6)
            xx += sz + 5
        yy += wys

        # co z CV pasuje, a czego brakuje
        d = self.dopasowania.get(ident) if self.cv else None
        if d and (d["pasuje"] or d["brakuje"]):
            yy += 6
            xx = lewo
            for znak, lista, kolor in (("✓ ", d["pasuje"], "#15803d"), ("brakuje: ", d["brakuje"], "#b91c1c")):
                if not lista:
                    continue
                tekst = znak + ", ".join(lista[:6]) + (" …" if len(lista) > 6 else "")
                szer = self.f_maly.measure(tekst)
                if xx > lewo and xx + szer > prawo:
                    xx, yy = lewo, yy + self.f_maly.metrics("linespace") + 2
                t = c.create_text(xx, yy, text=tekst, anchor="nw", fill=kolor, font=self.f_maly,
                                  width=prawo - lewo, tags=tagi)
                bb = c.bbox(t)
                xx = bb[2] + 16
                ostatni_dol = bb[3]
            yy = ostatni_dol

        rozwinieta = ident in self.rozwiniete
        if o.get("opis") and not (rozwinieta and ident in self.szczegoly):
            t = c.create_text(lewo, yy + 8, text=skroc(o["opis"], 230), anchor="nw",
                              fill=KOLORY["tekst"], font=self.f_maly, width=prawo - lewo,
                              tags=otw)
            yy = c.bbox(t)[3]

        # rozwijane szczegoly: obowiazki, wymagania...
        if ma_link:
            t = c.create_text(lewo, yy + 9, anchor="nw", fill=KOLORY["akcent"], font=self.f_link,
                              text="▴  Zwiń szczegóły" if rozwinieta else "▾  Obowiązki i wymagania",
                              tags=tagi + ("rozwin", "reka"))
            yy = c.bbox(t)[3]
            if rozwinieta:
                yy = self._rysuj_szczegoly(ident, o, lewo, prawo, yy + 8, tagi)

        yy += 12
        c.create_line(lewo, yy, prawo, yy, fill=KOLORY["linia"], tags=tagi)
        yy += 10

        # statusy
        pigulki = {}
        xx, wys = lewo, 0
        for kod, nazwa in STATUSY:
            szer = self.f_pigulka_b.measure(nazwa) + 22
            if xx + szer > prawo and xx > lewo:
                xx, yy = lewo, yy + wys + 6
            tlo, t, sz, wys = self._pigulka_na_plotnie(
                xx, yy, nazwa, KOLORY["tekst"], KOLORY["karta"], KOLORY["linia"],
                self.f_pigulka, tagi + ("status", "s_" + kod, "reka"))
            pigulki[kod] = (tlo, t)
            xx += sz + 6

        # link + notatka po prawej (albo w nowej linii, gdy brak miejsca)
        szer_notatki = min(260, max(160, (prawo - lewo) // 4))
        szer_linku = self.f_link.measure("Otwórz ofertę ↗") + 16 if ma_link else 0
        if xx + 10 + szer_linku + szer_notatki > prawo:
            yy += wys + 8
        xn = prawo - szer_notatki
        nota_tlo = self._zaokr(xn, yy, prawo, yy + wys, 6, fill=KOLORY["pole"],
                               outline=KOLORY["linia"], tags=tagi + ("nota",))
        nota_txt = c.create_text(xn + 8, yy + wys / 2, anchor="w", font=self.f_maly,
                                 tags=tagi + ("nota",))
        if ma_link:
            c.create_text(xn - 14, yy + wys / 2, text="Otwórz ofertę ↗", anchor="e",
                          fill=KOLORY["akcent"], font=self.f_link, tags=otw + ("link",))
        yy += wys + 14

        # tlo karty na pelna wysokosc
        c.coords(pasek, *self._punkty(x0, y, x1, yy, 8))
        c.coords(ramka, *self._punkty(x0 + 3, y, x1, yy, 8))

        self.karty[ident] = {"pasek": pasek, "ramka": ramka, "pigulki": pigulki, "nota": (nota_tlo, nota_txt),
                             "szer_notatki": szer_notatki}
        self._styl_karty(ident)
        self._pokaz_notatke(ident)
        return yy

    def _podepnij_plotno(self):
        """Obsluga myszy dla wszystkich kart - podpinana RAZ."""
        c = self.plotno
        c.tag_bind("otw", "<Button-1>", lambda _e: self._na_karcie(self.otworz_oferte))
        c.tag_bind("status", "<Button-1>", lambda _e: self._klik_statusu())
        c.tag_bind("nota", "<Button-1>", lambda _e: self._na_karcie(self._edytuj_notatke))
        c.tag_bind("karta", "<Button-3>",
                   lambda e: self._na_karcie(lambda i: self._menu_kontekstowe(e, i)))
        c.tag_bind("pager", "<Button-1>", lambda _e: self._klik_pager())
        c.tag_bind("karta", "<Enter>", lambda _e: self._podswietl(True))
        c.tag_bind("karta", "<Leave>", lambda _e: self._podswietl(False))
        c.tag_bind("reka", "<Enter>", lambda _e: self._przewija() or c.configure(cursor="hand2"),
                   add="+")
        c.tag_bind("reka", "<Leave>", lambda _e: c.configure(cursor=""), add="+")
        c.tag_bind("nota", "<Enter>", lambda _e: c.configure(cursor="xterm"), add="+")
        c.tag_bind("nota", "<Leave>", lambda _e: c.configure(cursor=""), add="+")
        c.tag_bind("rozwin", "<Button-1>", lambda _e: self._na_karcie(self.przelacz_szczegoly))
        c.tag_bind("ponow", "<Button-1>",
                   lambda _e: self._na_karcie(lambda i: self.pobierz_szczegoly(i)))
        for tag, zwykly, hover in (("tytul", self.f_oferta, self.f_oferta_hover),
                                   ("link", self.f_link, self.f_link_hover),
                                   ("rozwin", self.f_link, self.f_link_hover),
                                   ("ponow", self.f_etykieta_b, self.f_etykieta_b_hover)):
            c.tag_bind(tag, "<Enter>", lambda _e, f=hover: c.itemconfigure("current", font=f), add="+")
            c.tag_bind(tag, "<Leave>", lambda _e, f=zwykly: c.itemconfigure("current", font=f), add="+")

    def _nr_karty(self):
        for t in self.plotno.gettags("current"):
            if t[0] == "k" and t[1:].isdigit():
                nr = int(t[1:])
                if nr < len(self.widoczne):
                    return nr
        return None

    def _na_karcie(self, funkcja):
        nr = self._nr_karty()
        if nr is not None:
            funkcja(self.widoczne[nr]["id"])

    def _klik_statusu(self):
        kod = next((t[2:] for t in self.plotno.gettags("current") if t.startswith("s_")), None)
        if kod:
            self._na_karcie(lambda i: self.zmien_status(i, kod))

    def _podswietl(self, wlacz):
        c = self.plotno
        if wlacz and self._przewija():      # karta "przejechala" pod kursorem - pomijamy
            return
        if self.podswietlona is not None:
            c.itemconfigure(self.podswietlona, outline=KOLORY["linia"])
            self.podswietlona = None
        nr = self._nr_karty() if wlacz else None
        if nr is not None:
            k = self.karty.get(self.widoczne[nr]["id"])
            if k:
                c.itemconfigure(k["ramka"], outline=KOLORY["linia_hover"])
                self.podswietlona = k["ramka"]

    _LUK = [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in (0, 30, 60, 90)]

    def _punkty(self, x0, y0, x1, y1, r):
        """Prostokat z zaokraglonymi rogami jako lista punktow wielokata."""
        r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
        p = []
        for cx, cy, sx, sy, odwroc in ((x1 - r, y0 + r, 1, -1, True), (x1 - r, y1 - r, 1, 1, False),
                                       (x0 + r, y1 - r, -1, 1, True), (x0 + r, y0 + r, -1, -1, False)):
            luk = self._LUK[::-1] if odwroc else self._LUK
            for c, s in luk:
                p += [cx + sx * r * c, cy + sy * r * s]
        return p

    def _etykiety(self, o):
        e = []
        d = self.dopasowania.get(o["id"]) if self.cv else None
        if d:
            w = d["wynik"]
            e.append(("Dopasowanie %s%d%%" % ("~" if d.get("wstepny") else "", w),
                      "dop_wys" if w >= 70 else "dop_sr" if w >= 45 else "dop_nis"))
        if czy_nowa(o):
            e.append(("nowa", "nowa"))
        if not o["aktywna"]:
            e.append(("wygasła", "wygasla"))
        if o["zdalna"]:
            e.append(("zdalnie", "zdalnie"))
        if o.get("wynagrodzenie"):
            e.append((skroc(o["wynagrodzenie"], 50), "kasa"))
        for pole in ("umowa", "termin", "zrodlo", "kategoria"):
            if o.get(pole):
                e.append((skroc(o[pole], 40), "zwykla"))
        return e

    def _styl_karty(self, ident):
        k = self.karty.get(ident)
        o = self.oferty.get(ident)
        if not k or not o:
            return
        c = self.plotno
        status = o["status"] or "nowa"
        c.itemconfigure(k["pasek"], fill=KOLOR_STATUSU.get(status, KOLOR_STATUSU["nowa"]))
        for kod, (tlo, t) in k["pigulki"].items():
            if kod == status:
                fg, bg, ramka = AKTYWNY_STATUS[kod]
                c.itemconfigure(tlo, fill=bg, outline=ramka)
                c.itemconfigure(t, fill=fg, font=self.f_pigulka_b)
            else:
                c.itemconfigure(tlo, fill=KOLORY["karta"], outline=KOLORY["linia"])
                c.itemconfigure(t, fill=KOLORY["tekst"], font=self.f_pigulka)

    def _pokaz_notatke(self, ident):
        k, o = self.karty.get(ident), self.oferty.get(ident)
        if not k or not o:
            return
        tekst = " ".join((o.get("notatka") or "").split())
        miejsce = k["szer_notatki"] - 16
        if tekst and self.f_maly.measure(tekst) > miejsce:
            while tekst and self.f_maly.measure(tekst + "…") > miejsce:
                tekst = tekst[:-1]
            tekst += "…"
        self.plotno.itemconfigure(k["nota"][1], text=tekst or "notatka…",
                                  fill=KOLORY["tekst"] if tekst else KOLORY["slaby"])

    def _menu_kontekstowe(self, e, ident):
        self.menu_id = ident
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # ------------------------------------------------------------------ notatki
    def _edytuj_notatke(self, ident):
        self._koniec_edycji(True)
        k, o = self.karty.get(ident), self.oferty.get(ident)
        if not k:
            return
        x0, y0, x1, y1 = self.plotno.bbox(k["nota"][0])
        self.edytowana = ident
        self.edytor.delete(0, "end")
        self.edytor.insert(0, o.get("notatka") or "")
        self.okno_edytora = self.plotno.create_window(x0 + 1, y0 + 1, anchor="nw", window=self.edytor,
                                                      width=x1 - x0 - 2, height=y1 - y0 - 2)
        self.edytor.focus_set()
        self.edytor.icursor("end")

    def _notatka_pisze(self):
        if self.timer_notatki:
            self.root.after_cancel(self.timer_notatki)
        self.timer_notatki = self.root.after(800, self._zapisz_notatke)

    def _zapisz_notatke(self):
        if self.timer_notatki:
            self.root.after_cancel(self.timer_notatki)
            self.timer_notatki = None
        ident = self.edytowana
        o = self.oferty.get(ident)
        if not o:
            return True
        tekst = self.edytor.get()
        if tekst == (o.get("notatka") or ""):
            return True
        try:
            kiedy = baza.ustaw_notatke(self.conn, ident, tekst)
        except Exception as e:
            LOG.exception("Zapis notatki nie wyszedl")
            messagebox.showerror("Nie udało się zapisać",
                                 "Notatka NIE została zapisana.\n\n%s" % e)
            return False
        o["notatka"], o["zmieniono"] = tekst, kiedy
        self.l_stopka.configure(text="✓ Notatka zapisana: %s  ·  %s" % (
            skroc(o["tytul"], 60), datetime.now().strftime("%H:%M:%S")))
        return True

    def _koniec_edycji(self, zapisz):
        if self.edytowana is None:
            return
        if zapisz:
            self._zapisz_notatke()
        elif self.timer_notatki:
            self.root.after_cancel(self.timer_notatki)
            self.timer_notatki = None
        ident, self.edytowana = self.edytowana, None
        if self.okno_edytora:
            self.plotno.delete(self.okno_edytora)
            self.okno_edytora = None
        self.plotno.focus_set()
        self._pokaz_notatke(ident)
        if self.do_przerysowania:      # szczegoly przyszly w trakcie pisania notatki
            self.do_przerysowania = False
            self.root.after_idle(self._przerysuj)

    # ------------------------------------------------------------------ szczegoly oferty
    SEKCJE_SZCZEGOLOW = [("opis", "Opis"), ("obowiazki", "Obowiązki"), ("wymagania", "Wymagania"),
                         ("mile_widziane", "Mile widziane"), ("oferujemy", "Oferujemy")]

    def _rysuj_szczegoly(self, ident, o, lewo, prawo, y, tagi):
        """Rozwiniety panel pod opisem karty. Zwraca y jego dolnej krawedzi."""
        c = self.plotno
        tlo = self._zaokr(lewo, y, prawo, y + 10, 7, fill="#f9fafb", outline=KOLORY["linia"],
                          tags=tagi)
        x, szer = lewo + 14, prawo - lewo - 28
        yy = y + 12
        sz = self.szczegoly.get(ident)
        if ident in self.pobierane:
            t = c.create_text(x, yy, anchor="nw", font=self.f_maly, fill=KOLORY["slaby"], tags=tagi,
                              text="Pobieram opis oferty z %s…" % (o.get("zrodlo") or "serwisu"))
            yy = c.bbox(t)[3]
        elif ident in self.bledy_szczeg or not sz:
            t = c.create_text(x, yy, anchor="nw", font=self.f_maly, fill="#b91c1c", width=szer,
                              tags=tagi, text="Nie udało się pobrać opisu: %s."
                              % self.bledy_szczeg.get(ident, "brak danych"))
            yy = c.bbox(t)[3] + 6
            t = c.create_text(x, yy, anchor="nw", font=self.f_etykieta_b, fill=KOLORY["akcent"],
                              text="Spróbuj ponownie", tags=tagi + ("ponow", "reka"))
            yy = c.bbox(t)[3]
        else:
            for klucz, naglowek in self.SEKCJE_SZCZEGOLOW:
                pozycje = sz.get(klucz) or []
                if not pozycje:
                    continue
                t = c.create_text(x, yy, anchor="nw", text=naglowek, font=self.f_sekcja,
                                  fill=KOLORY["akcent_ciemny"], tags=tagi)
                yy = c.bbox(t)[3] + 4
                for tekst in pozycje:
                    if klucz == "opis":
                        t = c.create_text(x, yy, anchor="nw", text=tekst, width=szer,
                                          font=self.f_maly, fill=KOLORY["tekst"], tags=tagi)
                    else:
                        c.create_text(x + 2, yy, anchor="nw", text="•", font=self.f_maly,
                                      fill=KOLORY["slaby"], tags=tagi)
                        t = c.create_text(x + 16, yy, anchor="nw", text=tekst, width=szer - 16,
                                          font=self.f_maly, fill=KOLORY["tekst"], tags=tagi)
                    yy = c.bbox(t)[3] + 3
                yy += 8
            t = c.create_text(x, yy, anchor="nw", font=self.f_etykieta, fill=KOLORY["slaby"],
                              tags=tagi, text="Z %s, pobrano %s  ·  " % (
                                  o.get("zrodlo") or "serwisu", lokalny_czas(sz.get("pobrano"))))
            bb = c.bbox(t)
            c.create_text(bb[2], yy, anchor="nw", font=self.f_etykieta_b, fill=KOLORY["akcent"],
                          text="pobierz ponownie", tags=tagi + ("ponow", "reka"))
            yy = bb[3]
        yy += 12
        c.coords(tlo, *self._punkty(lewo, y, prawo, yy, 7))
        return yy

    def przelacz_szczegoly(self, ident):
        if ident not in self.oferty:
            return
        if ident in self.rozwiniete:
            self.rozwiniete.discard(ident)
            self._przerysuj()
        elif ident in self.szczegoly or ident in self.pobierane:
            self.rozwiniete.add(ident)
            self._przerysuj()
        else:
            self.pobierz_szczegoly(ident)

    def pobierz_szczegoly(self, ident):
        o = self.oferty.get(ident)
        if not o or not o.get("url") or ident in self.pobierane:
            return
        self.rozwiniete.add(ident)
        self.bledy_szczeg.pop(ident, None)
        self.pobierane.add(ident)
        self.kolejka_szczeg.put((ident, dict(o)))
        if not (self.watek_szczeg and self.watek_szczeg.is_alive()):
            self.watek_szczeg = threading.Thread(target=self._pracownik_szczegolow, daemon=True)
            self.watek_szczeg.start()
        self.l_stopka.configure(text="Pobieram opis oferty: %s…" % skroc(o["tytul"], 60))
        self._przerysuj()

    def _pracownik_szczegolow(self):
        # osobny watek: pobiera strony po kolei; do bazy pisze tylko watek okna
        while not self.przerwij.is_set():
            try:
                ident, oferta = self.kolejka_szczeg.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.kolejka.put(("szczegoly", (ident, radar.pobierz_szczegoly(oferta), None)))
            except urllib.error.HTTPError as e:
                opis = "serwis odpowiedział błędem %d" % e.code
                if e.code in (404, 410):
                    opis += " (oferta mogła wygasnąć)"
                self.kolejka.put(("szczegoly", (ident, None, opis)))
            except urllib.error.URLError:
                self.kolejka.put(("szczegoly", (ident, None, "brak połączenia z serwisem")))
            except TimeoutError:
                self.kolejka.put(("szczegoly", (ident, None, "serwis nie odpowiada")))
            except Exception as e:
                self.kolejka.put(("szczegoly", (ident, None, str(e) or e.__class__.__name__)))
            time.sleep(0.5)

    def _po_szczegolach(self, ident, dane, blad):
        self.pobierane.discard(ident)
        o = self.oferty.get(ident) or {}
        if dane:
            try:
                dane["pobrano"] = baza.zapisz_szczegoly(self.conn, ident, dane)
            except Exception:
                LOG.exception("Zapis szczegolow nie wyszedl")
            self.szczegoly[ident] = dane
            if self.profil and ident in self.oferty:      # dokladniejsze dopasowanie
                self.dopasowania[ident] = self.profil.ocen(self.oferty[ident], dane)
            LOG.info("Szczegoly oferty %s (%s): %s", ident, dane.get("metoda"),
                     ", ".join("%s=%d" % (k, len(v)) for k, v in dane.items() if isinstance(v, list)))
            self.l_stopka.configure(text="✓ Pobrano opis: %s" % skroc(o.get("tytul"), 60))
        else:
            self.bledy_szczeg[ident] = blad
            LOG.warning("Szczegoly oferty %s (%s) nie wyszly: %s", ident, o.get("url"), blad)
            self.l_stopka.configure(text="Nie udało się pobrać opisu: %s" % blad)
        if self._na_biezacej_stronie(ident):
            self._przerysuj()

    def _na_biezacej_stronie(self, ident):
        od = self.strona_nr * NA_STRONE
        return any(o["id"] == ident for o in self.widoczne[od:od + NA_STRONE])

    def _przerysuj(self):
        """Rysuje strone od nowa, nie ruszajac widoku (karta rosnie w dol)."""
        if self.edytowana is not None:     # nie przerywamy pisania notatki
            self.do_przerysowania = True
            return
        gora = self.plotno.canvasy(0)
        self._rozloz()
        wys = float(self.plotno.cget("scrollregion").split()[3])
        self.plotno.yview_moveto(gora / wys if wys else 0)

    def _stopka_domyslna(self):
        self.l_stopka.configure(
            text="Widać %d z %d ofert  ·  strona %d z %d  (Ctrl+← / Ctrl+→)  ·  "
                 "statusy zapisują się w pliku radar.db"
                 % (len(self.widoczne), len(self.oferty), self.strona_nr + 1, self.liczba_stron()))

    def _liczniki(self):
        wart = {"aktywne": 0, "nowe": 0, "do_zrobienia": 0, "zaaplikowana": 0, "odrzucona": 0}
        for o in self.oferty.values():
            status = o["status"] or "nowa"
            if o["aktywna"]:
                wart["aktywne"] += 1
                if czy_nowa(o) and status == "nowa":
                    wart["nowe"] += 1
            if status in wart:
                wart[status] += 1
        for k, w in self.liczniki.items():
            w.configure(text=str(wart[k]))

    # ------------------------------------------------------------------ karta oferty
    # ------------------------------------------------------------------ zmiany
    def zmien_status(self, ident, kod):
        o = self.oferty.get(ident)
        if not o:
            return
        self._koniec_edycji(True)   # zeby nie zgubic wpisywanej notatki
        try:
            kiedy = baza.ustaw_status(self.conn, ident, kod)
        except Exception as e:
            LOG.exception("Zapis statusu nie wyszedl")
            messagebox.showerror("Nie udało się zapisać",
                                 "Status NIE został zapisany.\n\n%s\n\nSzczegóły w pliku radar.log." % e)
            return
        o["status"], o["zmieniono"] = kod, kiedy
        self._styl_karty(ident)
        self._liczniki()
        self.l_stopka.configure(text="✓ Status zapisany (%s): %s  ·  %s" % (
            NAZWA_STATUSU[kod], skroc(o["tytul"], 60), datetime.now().strftime("%H:%M:%S")))

    def otworz_oferte(self, ident):
        o = self.oferty.get(ident)
        if not o or not o.get("url"):
            return
        try:
            webbrowser.open(o["url"], new=2)
        except Exception as e:
            LOG.exception("Nie udalo sie otworzyc przegladarki")
            messagebox.showerror("Nie udało się otworzyć",
                                 "Nie udało się otworzyć przeglądarki.\n\n%s\n\nLink:\n%s" % (e, o["url"]))
            return
        self.l_stopka.configure(text="Otwarto w przeglądarce: %s" % o["url"])

    def kopiuj_link(self, ident):
        o = self.oferty.get(ident)
        if not o or not o.get("url"):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(o["url"])
        self.l_stopka.configure(text="Skopiowano link: %s" % o["url"])

    # ------------------------------------------------------------------ CV
    def _wczytaj_cv(self):
        try:
            cv = json.loads(baza.meta_get(self.conn, "cv") or "null")
        except ValueError:
            cv = None
        if isinstance(cv, dict) and (cv.get("tekst") or cv.get("umiejetnosci")):
            self.cv = cv

    def _opis_przycisku_cv(self):
        if hasattr(self, "b_cv"):
            self.b_cv.configure(text="Moje CV  ✓" if self.cv else "Dodaj CV")

    def _przelicz_dopasowania(self):
        self.dopasowania = {}
        self.profil = None
        if not self.cv:
            return
        teksty = []
        for o in self.oferty.values():
            sz = self.szczegoly.get(o["id"]) or {}
            teksty.append(" ".join([o.get("tytul") or "", o.get("opis") or ""] +
                                   [" ".join(v) for v in sz.values() if isinstance(v, list)]))
        try:
            self.profil = dopasowanie.Profil(self.cv.get("tekst", ""), self.cv.get("umiejetnosci", []),
                                             teksty)
            for o in self.oferty.values():
                self.dopasowania[o["id"]] = self.profil.ocen(o, self.szczegoly.get(o["id"]))
        except Exception:
            LOG.exception("Dopasowanie do CV nie wyszlo")
            self.dopasowania = {}

    def otworz_cv(self):
        if getattr(self, "okno_cv", None) and self.okno_cv.winfo_exists():
            self.okno_cv.lift()
            return
        self.okno_cv = OknoCV(self)

    def zapisz_cv(self, cv):
        """cv = None usuwa CV. Zwraca True, gdy zapis sie udal."""
        pierwsze = not self.cv
        try:
            if cv is None:
                baza.meta_set(self.conn, "cv", "")
            else:
                cv["zapisano"] = baza.teraz()
                baza.meta_set(self.conn, "cv", json.dumps(cv, ensure_ascii=False))
        except Exception as e:
            LOG.exception("Zapis CV nie wyszedl")
            messagebox.showerror("Nie udało się zapisać", "CV NIE zostało zapisane.\n\n%s" % e)
            return False
        self.cv = cv
        if not cv:
            self.sortowanie = "data"
            self.f_widok.discard("dopasowane")
        elif pierwsze:
            self.sortowanie = "cv"                  # od razu pokaz najlepiej dopasowane
        self._zapisz_filtry()
        self._opis_przycisku_cv()
        self._chipy("widok", self._widoki())
        self._przelicz_dopasowania()
        self.rysuj()
        LOG.info("CV %s (%d umiejetnosci)", "usuniete" if not cv else "zapisane",
                 len((cv or {}).get("umiejetnosci", [])))
        self.l_stopka.configure(text="✓ CV zapisane - oferty mają teraz wynik dopasowania" if cv
                                else "CV usunięte")
        if cv:
            dodane = self.dodaj_frazy_z_cv()
            okno = self.okno_cv if getattr(self, "okno_cv", None) and self.okno_cv.winfo_exists() \
                else self.root
            if dodane and messagebox.askyesno(
                    "Frazy z CV",
                    "Na podstawie CV będę szukać ofert po:\n\n• %s\n\nZmienisz je w oknie "
                    "„Ustawienia” (kategoria „Z CV”) - możesz też dopisać własne.\n\n"
                    "Pobrać teraz oferty?" % "\n• ".join(dodane), parent=okno):
                self.root.after(100, self.odswiez)
        return True

    def dodaj_frazy_z_cv(self, wymus=False):
        """Dopisuje do ustawien frazy wyszukiwania wyciagniete z CV (kategoria "Z CV").
        Poprzednie frazy "Z CV" zastepuje nowymi; frazy wpisane recznie zostaja.
        Zwraca liste dodanych fraz."""
        if not self.cv:
            return []
        tekst = self.cv.get("tekst", "")
        umiejetnosci = self.cv.get("umiejetnosci", [])
        klucz = hashlib.md5((tekst + "|" + "|".join(umiejetnosci)).encode("utf-8")).hexdigest()
        u = json.loads(json.dumps(self.ustawienia))
        if u.get("cv_klucz") == klucz and not wymus:
            return []                        # z tego CV frazy juz byly dodane
        frazy = [f for f in u["frazy"] if not f.get("z_cv")]
        istniejace = {dopasowanie.uprosc(f["fraza"]) for f in frazy}
        limit = radar.LINKEDIN_MAX_ZAPYTAN // max(1, len(radar.LINKEDIN_LOKALIZACJE))
        w_linkedin = sum("LinkedIn" in f.get("zrodla", []) for f in frazy)
        dodane = []
        for fraza, typ in dopasowanie.frazy_z_cv(tekst, umiejetnosci):
            if dopasowanie.uprosc(fraza) in istniejace:
                continue
            zrodla = ["pracuj.pl", "RocketJobs", "OLX"]
            if typ == "stanowisko" and w_linkedin < limit:     # LinkedIn ma limit zapytan
                zrodla.insert(1, "LinkedIn")
                w_linkedin += 1
            frazy.append({"fraza": fraza, "kategoria": "Z CV", "zrodla": zrodla, "z_cv": True})
            dodane.append(fraza)
        u["frazy"], u["cv_klucz"] = frazy, klucz
        try:
            u = radar.zastosuj_ustawienia(u)
            baza.meta_set(self.conn, "ustawienia", json.dumps(u, ensure_ascii=False))
        except Exception:
            LOG.exception("Zapis fraz z CV nie wyszedl")
            radar.zastosuj_ustawienia(self.ustawienia)
            return []
        self.ustawienia = u
        LOG.info("Frazy z CV: %s", ", ".join(dodane) or "(brak nowych)")
        return dodane

    # ------------------------------------------------------------------ ustawienia
    def otworz_ustawienia(self, zakladka=None):
        """zakladka: 0 - frazy, 1 - filtry, 2 - lokalizacja i zrodla."""
        if getattr(self, "okno_ustawien", None) and self.okno_ustawien.winfo_exists():
            self.okno_ustawien.lift()
        else:
            self.okno_ustawien = OknoUstawien(self)
        if zakladka is not None:
            self.okno_ustawien.zakladki.select(zakladka)

    def brak_filtrow(self):
        """True, gdy nic nie zawęża wyników: bez słów wykluczających i języków,
        wszystkie poziomy stanowisk i bez miasta."""
        u = self.ustawienia
        return (not u.get("stop_tytul") and not u.get("stop_jezyk") and not u.get("miasto")
                and set(u.get("poziomy", [])) >= {k for k, _, _ in radar.POZIOMY})

    def zapisz_ustawienia(self, u):
        """Zapisuje ustawienia w bazie i od razu je stosuje. Zwraca True, gdy sie udalo."""
        try:
            u = radar.zastosuj_ustawienia(u)
            baza.meta_set(self.conn, "ustawienia", json.dumps(u, ensure_ascii=False))
            usunietych = baza.usun_niepasujace(self.conn, radar.pasuje_do_profilu,
                                               radar.pasuje_zasieg)
        except Exception as e:
            LOG.exception("Zapis ustawien nie wyszedl")
            messagebox.showerror("Nie udało się zapisać",
                                 "Ustawienia NIE zostały zapisane.\n\n%s" % e)
            radar.zastosuj_ustawienia(self.ustawienia)
            return False
        self.ustawienia = u
        LOG.info("Zapisano ustawienia: %d fraz, miasto %s, usunieto %d niepasujacych ofert",
                 len(u["frazy"]), u["miasto"], usunietych)
        self._chipy("widok", self._widoki())
        self.przeladuj()
        self.l_stopka.configure(text="✓ Ustawienia zapisane  ·  %s" % datetime.now().strftime("%H:%M:%S"))
        tekst = "Ustawienia zapisane."
        if usunietych:
            tekst += ("\n\nUsunięto %d ofert, które nie pasują do nowych filtrów "
                      "(oferty z Twoim statusem albo notatką zostają zawsze)." % usunietych)
        tekst += "\n\nPobrać teraz oferty według nowych ustawień?"
        if messagebox.askyesno("Radar Karier", tekst, parent=self.okno_ustawien):
            self.odswiez()
        return True

    def zacznij_od_zera(self):
        """Dla nowej osoby: usuwa oferty, statusy, notatki i opisy. Ustawienia zostaja."""
        if self.watek and self.watek.is_alive():
            messagebox.showinfo("Radar Karier", "Poczekaj, aż skończy się pobieranie ofert.",
                                parent=self.okno_ustawien)
            return False
        if not messagebox.askyesno(
                "Zacząć od zera?",
                "To usunie WSZYSTKIE oferty z bazy razem ze statusami, notatkami, "
                "pobranymi opisami i zapisanym CV. Ustawienia wyszukiwania zostaną.\n\n"
                "Wcześniej zrobię kopię bazy w folderze \"kopie\".\n\nNa pewno usunąć?",
                icon="warning", default="no", parent=self.okno_ustawien):
            return False
        self._koniec_edycji(False)
        try:
            baza.wyczysc_oferty(self.conn, KATALOG_KOPII)
        except Exception as e:
            LOG.exception("Czyszczenie bazy nie wyszlo")
            messagebox.showerror("Błąd", "Nie udało się wyczyścić bazy.\n\n%s" % e)
            return False
        LOG.info("Baza wyczyszczona (zacznij od zera)")
        self.rozwiniete.clear()
        self.bledy_szczeg.clear()
        self.cv = None
        self.f_zrodla, self.f_kategorie, self.f_statusy = set(), set(), set()
        self.f_widok, self.sortowanie = set(self.DOMYSLNY_WIDOK), "data"
        self._opis_przycisku_cv()
        self._chipy("widok", self._widoki())
        self.przeladuj()
        return True

    # ------------------------------------------------------------------ odswiezanie
    def odswiez(self):
        if self.watek and self.watek.is_alive():
            return
        if not self.ustawienia.get("frazy") and self.cv:
            self.dodaj_frazy_z_cv(wymus=True)           # szukamy po tym, co jest w CV
        if not self.ustawienia.get("frazy") and not radar.WLACZONE_ZRODLA.get("WorkConnect"):
            messagebox.showinfo("Czego szukać?",
                                "Nie ma jeszcze żadnych stanowisk ani słów kluczowych.\n\n"
                                "Dodaj CV (przycisk „Dodaj CV”) - frazy wezmę z niego - albo "
                                "wpisz je w oknie „Ustawienia” (zakładka „Stanowiska i słowa "
                                "kluczowe”), zapisz i pobierz oferty.")
            self.otworz_ustawienia()
            return
        if self.brak_filtrow():
            okno = OknoFiltrow(self)
            self.root.wait_window(okno)
            if okno.wybor == "filtry":
                self.otworz_ustawienia(zakladka=1)
                return
            if okno.wybor != "pomin":             # zamkniete okienko - nic nie robimy
                return
        self.przerwij.clear()
        self.b_odswiez.configure(text="Pobieram…", bg=KOLORY["wylaczony"], cursor="watch")
        self.pasek.pack(side="right", padx=(0, 12))
        self.pasek.start(12)
        self.l_stopka.configure(text="Pobieram świeże oferty…")
        pomin = self._pomin_linkedin()
        self.start_odswiezania = time.time()

        def praca():
            try:
                wynik = radar.zbierz_wszystko(
                    log=lambda m: self.kolejka.put(("log", m)), przerwij=self.przerwij, pomin=pomin)
                self.kolejka.put(("wynik", wynik))
            except Exception:
                self.kolejka.put(("blad", traceback.format_exc()))

        self.watek = threading.Thread(target=praca, daemon=True)
        self.watek.start()

    def _obsluz_kolejke(self):
        try:
            while True:
                rodzaj, dane = self.kolejka.get_nowait()
                if rodzaj == "log":
                    LOG.info(dane)
                    self.l_stopka.configure(text=dane)
                elif rodzaj == "wynik":
                    self._po_odswiezeniu(dane)
                elif rodzaj == "szczegoly":
                    self._po_szczegolach(*dane)
                elif rodzaj == "blad":
                    LOG.error(dane)
                    self._koniec_odswiezania()
                    self.l_stopka.configure(text="Odświeżanie nie wyszło - szczegóły w radar.log")
        except queue.Empty:
            pass
        self.root.after(150, self._obsluz_kolejke)

    # --- LinkedIn: najwyzej raz na kilka godzin, po odmowie dluzsza przerwa --------
    def _pomin_linkedin(self):
        """{"LinkedIn": powod}, gdy tym razem nie pytamy LinkedIn; inaczej {}."""
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
        pauza_do = czas("linkedin_pauza_do")
        if teraz < pauza_do:
            return {"LinkedIn": "wcześniej odmówił dostępu, przerwa do %s" % godzina(pauza_do)}
        nastepny = czas("linkedin_ostatnio") + radar.LINKEDIN_CO_ILE_GODZIN * 3600
        if teraz < nastepny:
            return {"LinkedIn": "był pytany niedawno, następny raz po %s" % godzina(nastepny)}
        return {}

    def _po_linkedin(self, wynik):
        try:
            if "LinkedIn" in (wynik.get("pytane_zrodla") or []):
                baza.meta_set(self.conn, "linkedin_ostatnio", str(self.start_odswiezania))
            if "LinkedIn" in (wynik.get("odmowy") or []):
                baza.meta_set(self.conn, "linkedin_pauza_do",
                              str(time.time() + radar.LINKEDIN_PAUZA_GODZIN * 3600))
        except Exception:
            LOG.exception("Zapis stanu LinkedIn nie wyszedl")

    def _po_odswiezeniu(self, wynik):
        # scalanie robi TYLKO watek okna - jedna droga zapisu, zero wyscigow
        self._koniec_edycji(True)
        self._po_linkedin(wynik)
        try:
            nowych, wygaszonych, usunietych = baza.scal(self.conn, wynik, radar.pasuje_do_profilu,
                                                        radar.pasuje_zasieg)
        except Exception as e:
            LOG.exception("Scalanie nie wyszlo")
            messagebox.showerror("Błąd bazy", "Nie udało się zapisać nowych ofert.\n\n%s" % e)
            self._koniec_odswiezania()
            return
        for tytul, powod in wynik.get("odrzucone", [])[:30]:
            LOG.info("odrzucona: %s [%s]", tytul, powod)
        self._koniec_odswiezania()
        self.przeladuj()
        tekst = "Gotowe: %d nowych, %d wygasło" % (nowych, wygaszonych)
        if "LinkedIn" in (wynik.get("odmowy") or []):
            tekst += "  ·  LinkedIn ograniczył dostęp - spróbuję za %d h" % radar.LINKEDIN_PAUZA_GODZIN
        elif (radar.WLACZONE_ZRODLA.get("LinkedIn")
              and "LinkedIn" not in (wynik.get("pytane_zrodla") or [])):
            powod = self._pomin_linkedin().get("LinkedIn")
            if powod:
                tekst += "  ·  LinkedIn pominięty: " + powod
        if wynik.get("bledy"):
            tekst += "  ·  błędów: %d (szczegóły w radar.log)" % len(wynik["bledy"])
            for b in wynik["bledy"]:
                LOG.warning(b)
        self.l_stopka.configure(text=tekst)

    def _koniec_odswiezania(self):
        self.pasek.stop()
        self.pasek.pack_forget()
        self.b_odswiez.configure(text="Odśwież oferty", bg=KOLORY["akcent"], cursor="hand2")

    # ------------------------------------------------------------------ koniec
    def zamknij(self):
        try:
            self._koniec_edycji(True)
        finally:
            self.przerwij.set()
            try:
                self.conn.close()
            except Exception:
                pass
            self.root.destroy()


class OknoUstawien(tk.Toplevel):
    """Okno "Ustawienia": frazy / stanowiska, filtry, lokalizacja i zrodla."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Ustawienia wyszukiwania — Radar Karier")
        self.configure(bg=KOLORY["tlo"])
        self.geometry("900x680")
        self.minsize(760, 560)
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", self.anuluj)
        self.f = app.f_zwykly

        s = ttk.Style()
        s.configure("Ust.TNotebook", background=KOLORY["tlo"], borderwidth=0)
        s.configure("Ust.TNotebook.Tab", padding=(14, 6), font=app.f_pigulka)
        s.map("Ust.TNotebook.Tab", font=[("selected", app.f_pigulka_b)])
        s.configure("Ust.TFrame", background=KOLORY["karta"])
        s.configure("Ust.TCheckbutton", background=KOLORY["karta"], font=app.f_zwykly)
        s.configure("Ust.Treeview", rowheight=26, font=app.f_zwykly)
        s.configure("Ust.Treeview.Heading", font=app.f_pigulka_b)

        # przyciski na dole
        dol = tk.Frame(self, bg=KOLORY["tlo"])
        dol.pack(side="bottom", fill="x", padx=16, pady=(8, 14))
        self._przycisk(dol, "Zapisz", self.zapisz, glowny=True).pack(side="right")
        self._przycisk(dol, "Anuluj", self.anuluj).pack(side="right", padx=(0, 8))
        self._przycisk(dol, "Przywróć domyślne", self.domyslne).pack(side="left")

        zakladki = ttk.Notebook(self, style="Ust.TNotebook")
        zakladki.pack(fill="both", expand=True, padx=16, pady=(14, 0))
        self.zakladki = zakladki
        self._zakladka_fraz(zakladki)
        self._zakladka_filtrow(zakladki)
        self._zakladka_lokalizacji(zakladki)

        self.wczytaj(app.ustawienia)
        self.bind("<Escape>", lambda _e: self.anuluj())
        self.after(50, self.e_fraza.focus_set)

    # --- klocki ---------------------------------------------------------
    def _przycisk(self, master, tekst, komenda, glowny=False):
        a = self.app
        bg, fg = (KOLORY["akcent"], "white") if glowny else (KOLORY["karta"], KOLORY["tekst"])
        b = tk.Label(master, text=tekst, font=a.f_przycisk if glowny else a.f_pigulka,
                     bg=bg, fg=fg, padx=16, pady=6, cursor="hand2", highlightthickness=1,
                     highlightbackground=KOLORY["akcent"] if glowny else KOLORY["linia"])
        b.bind("<Button-1>", lambda _e: komenda())
        return b

    def _napis(self, master, tekst, slaby=False, **kw):
        return tk.Label(master, text=tekst, bg=KOLORY["karta"], justify="left", anchor="w",
                        fg=KOLORY["slaby"] if slaby else KOLORY["tekst"],
                        font=self.app.f_maly if slaby else self.app.f_zwykly, **kw)

    def _naglowek(self, master, tekst):
        return tk.Label(master, text=tekst, bg=KOLORY["karta"], fg=KOLORY["tekst"],
                        font=self.app.f_sekcja, anchor="w")

    def _pole_listy(self, master, wysokosc):
        ramka = tk.Frame(master, bg=KOLORY["karta"])
        t = tk.Text(ramka, height=wysokosc, width=30, wrap="none", font=self.app.f_zwykly,
                    relief="flat", bg=KOLORY["pole"], highlightthickness=1,
                    highlightbackground=KOLORY["linia"], highlightcolor=KOLORY["akcent"],
                    padx=6, pady=4, undo=True)
        pas = ttk.Scrollbar(ramka, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=pas.set)
        pas.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        return ramka, t

    @staticmethod
    def _lista_z_pola(t):
        wynik = []
        for linia in t.get("1.0", "end").replace(",", "\n").splitlines():
            x = " ".join(linia.split())
            if x and x.lower() not in [w.lower() for w in wynik]:
                wynik.append(x)
        return wynik

    @staticmethod
    def _pole_z_listy(t, lista):
        t.delete("1.0", "end")
        t.insert("1.0", "\n".join(lista))
        t.edit_reset()

    # --- zakladka 1: stanowiska / slowa kluczowe --------------------------
    def _zakladka_fraz(self, zakladki):
        z = tk.Frame(zakladki, bg=KOLORY["karta"], padx=16, pady=14)
        zakladki.add(z, text="Stanowiska i słowa kluczowe")
        self._napis(z, "Aplikacja szuka ofert po tych frazach. Wpisz nazwę stanowiska albo słowo "
                       "kluczowe, np. „specjalista ds. marketingu”, „google ads”, „przedstawiciel "
                       "handlowy”. Kategoria to Twoja etykieta — pojawi się w filtrach na liście ofert.",
                    slaby=True, wraplength=820).pack(fill="x")

        form = tk.Frame(z, bg=KOLORY["karta"])
        form.pack(fill="x", pady=(12, 8))
        self._napis(form, "Fraza / stanowisko").grid(row=0, column=0, sticky="w")
        self._napis(form, "Kategoria").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.v_fraza = tk.StringVar()
        self.e_fraza = tk.Entry(form, textvariable=self.v_fraza, font=self.app.f_zwykly, width=30,
                                relief="flat", bg=KOLORY["pole"], highlightthickness=1,
                                highlightbackground=KOLORY["linia"], highlightcolor=KOLORY["akcent"])
        self.e_fraza.grid(row=1, column=0, sticky="we", ipady=4)
        self.e_fraza.bind("<Return>", lambda _e: self.dodaj_fraze())
        self.v_kategoria = tk.StringVar()
        self.c_kategoria = ttk.Combobox(form, textvariable=self.v_kategoria, width=20,
                                        font=self.app.f_zwykly)
        self.c_kategoria.grid(row=1, column=1, sticky="we", padx=(10, 0), ipady=2)
        self.c_kategoria.bind("<Return>", lambda _e: self.dodaj_fraze())
        wiersz = tk.Frame(form, bg=KOLORY["karta"])
        wiersz.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._napis(wiersz, "Szukaj w:").pack(side="left", padx=(0, 8))
        self.v_zrodla_frazy = {}
        # nowa fraza: domyslnie bez LinkedIn (ma limit zapytan) i bez Useme (to zlecenia)
        for zr in radar.ZRODLA_FRAZ:
            v = tk.BooleanVar(value=zr not in ("LinkedIn", "Useme"))
            ttk.Checkbutton(wiersz, text=zr, variable=v, style="Ust.TCheckbutton").pack(
                side="left", padx=(0, 10))
            self.v_zrodla_frazy[zr] = v
        form.columnconfigure(0, weight=1)

        przyciski = tk.Frame(z, bg=KOLORY["karta"])
        przyciski.pack(fill="x", pady=(0, 10))
        self._przycisk(przyciski, "+ Dodaj frazę", self.dodaj_fraze, glowny=True).pack(side="left")
        self.b_zmien = self._przycisk(przyciski, "Zapisz zmianę w zaznaczonej", self.zmien_fraze)
        self.b_zmien.pack(side="left", padx=(8, 0))
        self._przycisk(przyciski, "Usuń zaznaczoną", self.usun_fraze).pack(side="left", padx=(8, 0))
        self.l_frazy = self._napis(przyciski, "", slaby=True)
        self.l_frazy.pack(side="right")

        tabela = tk.Frame(z, bg=KOLORY["karta"])
        tabela.pack(fill="both", expand=True)
        kolumny = [("fraza", "Fraza / stanowisko", 210), ("kategoria", "Kategoria", 150)] + \
                  [(zr, zr, 92) for zr in radar.ZRODLA_FRAZ]
        self.t_frazy = ttk.Treeview(tabela, columns=[k for k, _, _ in kolumny], show="headings",
                                    selectmode="browse", style="Ust.Treeview")
        for k, n, w in kolumny:
            self.t_frazy.heading(k, text=n)
            self.t_frazy.column(k, width=w, stretch=k in ("fraza", "kategoria"),
                                anchor="w" if k in ("fraza", "kategoria") else "center")
        pas = ttk.Scrollbar(tabela, orient="vertical", command=self.t_frazy.yview)
        self.t_frazy.configure(yscrollcommand=pas.set)
        pas.pack(side="right", fill="y")
        self.t_frazy.pack(side="left", fill="both", expand=True)
        self.t_frazy.bind("<<TreeviewSelect>>", lambda _e: self._wybrana_fraza())
        self.t_frazy.bind("<Delete>", lambda _e: self.usun_fraze())
        self.frazy = []

    def _rysuj_frazy(self, zaznacz=None):
        t = self.t_frazy
        t.delete(*t.get_children())
        for i, f in enumerate(self.frazy):
            t.insert("", "end", iid=str(i), values=[f["fraza"], f["kategoria"]] +
                     ["✓" if zr in f["zrodla"] else "—" for zr in radar.ZRODLA_FRAZ])
        if zaznacz is not None and 0 <= zaznacz < len(self.frazy):
            t.selection_set(str(zaznacz))
            t.see(str(zaznacz))
        kategorie = sorted({f["kategoria"] for f in self.frazy if f["kategoria"]})
        self.c_kategoria.configure(values=kategorie)
        self._licznik_fraz()

    def _licznik_fraz(self):
        if not hasattr(self, "v_zdalne"):
            return
        w_linkedin = sum("LinkedIn" in f["zrodla"] for f in self.frazy)
        miasto = self.v_miasto.get().strip() if hasattr(self, "v_miasto") else ""
        limit = radar.LINKEDIN_MAX_ZAPYTAN // (2 if self.v_zdalne.get() and miasto else 1)
        self.l_frazy.configure(
            text="%d fraz  ·  LinkedIn: %d z max %d%s" % (
                len(self.frazy), w_linkedin, limit, "  (nadmiar pominięty)" if w_linkedin > limit else ""),
            fg="#b91c1c" if w_linkedin > limit else KOLORY["slaby"])

    def _wybrana_fraza(self):
        sel = self.t_frazy.selection()
        if not sel:
            return
        f = self.frazy[int(sel[0])]
        self.v_fraza.set(f["fraza"])
        self.v_kategoria.set(f["kategoria"])
        for zr, v in self.v_zrodla_frazy.items():
            v.set(zr in f["zrodla"])

    def _fraza_z_formularza(self):
        fraza = " ".join(self.v_fraza.get().split())
        if not fraza:
            messagebox.showinfo("Brak frazy", "Wpisz nazwę stanowiska albo słowo kluczowe.", parent=self)
            self.e_fraza.focus_set()
            return None
        zrodla = [zr for zr, v in self.v_zrodla_frazy.items() if v.get()]
        if not zrodla:
            messagebox.showinfo("Brak serwisu", "Zaznacz przynajmniej jeden serwis, w którym szukać.",
                                parent=self)
            return None
        kategoria = " ".join(self.v_kategoria.get().split()) or "Inne"
        return {"fraza": fraza, "kategoria": kategoria, "zrodla": zrodla}

    def dodaj_fraze(self):
        f = self._fraza_z_formularza()
        if not f:
            return
        for i, stara in enumerate(self.frazy):
            if stara["fraza"].lower() == f["fraza"].lower():
                messagebox.showinfo("Taka fraza już jest",
                                    "„%s” jest już na liście — zaznaczyłem ją. Zmień ją i kliknij "
                                    "„Zapisz zmianę w zaznaczonej”." % stara["fraza"], parent=self)
                self._rysuj_frazy(zaznacz=i)
                return
        self.frazy.append(f)
        self._rysuj_frazy(zaznacz=len(self.frazy) - 1)
        self.v_fraza.set("")
        self.e_fraza.focus_set()

    def zmien_fraze(self):
        sel = self.t_frazy.selection()
        if not sel:
            messagebox.showinfo("Nic nie zaznaczono", "Zaznacz frazę na liście.", parent=self)
            return
        f = self._fraza_z_formularza()
        if not f:
            return
        i = int(sel[0])
        if any(j != i and s["fraza"].lower() == f["fraza"].lower() for j, s in enumerate(self.frazy)):
            messagebox.showinfo("Taka fraza już jest", "Ta fraza jest już na liście.", parent=self)
            return
        self.frazy[i] = f
        self._rysuj_frazy(zaznacz=i)

    def usun_fraze(self):
        sel = self.t_frazy.selection()
        if not sel:
            return
        i = int(sel[0])
        del self.frazy[i]
        self._rysuj_frazy(zaznacz=min(i, len(self.frazy) - 1))
        self.v_fraza.set("")

    # --- zakladka 2: filtry ------------------------------------------------
    def _zakladka_filtrow(self, zakladki):
        z = tk.Frame(zakladki, bg=KOLORY["karta"], padx=16, pady=14)
        zakladki.add(z, text="Filtry")
        z.columnconfigure(0, weight=3)
        z.columnconfigure(1, weight=2)
        z.rowconfigure(2, weight=1)

        self._naglowek(z, "Pomijaj oferty, których tytuł zawiera").grid(row=0, column=0, sticky="w")
        self._napis(z, "Jedno słowo lub fraza w linii (albo po przecinku), np. senior, kierownik, "
                       "developer. Wielkość liter i polskie znaki nie mają znaczenia.",
                    slaby=True, wraplength=440).grid(row=1, column=0, sticky="w", pady=(2, 6))
        ramka, self.t_stop = self._pole_listy(z, 14)
        ramka.grid(row=2, column=0, sticky="nsew", padx=(0, 20))

        prawo = tk.Frame(z, bg=KOLORY["karta"])
        prawo.grid(row=0, column=1, rowspan=3, sticky="nsew")
        self._naglowek(prawo, "Poziomy stanowisk, które Cię interesują").pack(fill="x")
        self._napis(prawo, "Dotyczy ofert z pracuj.pl, które podają poziom.", slaby=True).pack(
            fill="x", pady=(2, 6))
        self.v_poziomy = {}
        for klucz, nazwa, _ in radar.POZIOMY:
            v = tk.BooleanVar()
            ttk.Checkbutton(prawo, text=nazwa, variable=v, style="Ust.TCheckbutton").pack(anchor="w")
            self.v_poziomy[klucz] = v
        self._naglowek(prawo, "Pomijaj oferty wymagające języków").pack(fill="x", pady=(14, 0))
        self._napis(prawo, "Szukane w tytule oferty, np. german, niemiecki.", slaby=True).pack(
            fill="x", pady=(2, 6))
        ramka, self.t_jezyki = self._pole_listy(prawo, 5)
        ramka.pack(fill="both", expand=True)

    # --- zakladka 3: lokalizacja i zrodla -------------------------------------
    def _zakladka_lokalizacji(self, zakladki):
        z = tk.Frame(zakladki, bg=KOLORY["karta"], padx=16, pady=14)
        zakladki.add(z, text="Lokalizacja i źródła")
        z.columnconfigure(0, weight=1)
        z.columnconfigure(1, weight=1)

        lewo = tk.Frame(z, bg=KOLORY["karta"])
        lewo.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        self._naglowek(lewo, "Miasto").pack(fill="x")
        self._napis(lewo, "Puste = oferty z całej Polski.", slaby=True).pack(fill="x", pady=(2, 0))
        self.v_miasto = tk.StringVar()
        self.v_miasto.trace_add("write", lambda *_: self._licznik_fraz())   # zmienia limit LinkedIn
        tk.Entry(lewo, textvariable=self.v_miasto, font=self.app.f_zwykly, relief="flat",
                 bg=KOLORY["pole"], highlightthickness=1, highlightbackground=KOLORY["linia"],
                 highlightcolor=KOLORY["akcent"]).pack(fill="x", ipady=4, pady=(4, 12))
        self._naglowek(lewo, "Miejscowości w okolicy").pack(fill="x")
        self._napis(lewo, "Oferty z tych miejscowości też się liczą. Jedna w linii.",
                    slaby=True).pack(fill="x", pady=(2, 6))
        ramka, self.t_okolice = self._pole_listy(lewo, 10)
        ramka.pack(fill="both", expand=True)
        self.v_zdalne = tk.BooleanVar()
        self.v_zdalne.trace_add("write", lambda *_: self._licznik_fraz())   # zmienia limit LinkedIn
        ttk.Checkbutton(lewo, text="Pokazuj też oferty zdalne z całej Polski\n"
                                   "(w tym zlecenia z Useme i WorkConnect)",
                        variable=self.v_zdalne, style="Ust.TCheckbutton").pack(anchor="w", pady=(10, 0))

        prawo = tk.Frame(z, bg=KOLORY["karta"])
        prawo.grid(row=0, column=1, sticky="nsew")
        self._naglowek(prawo, "Przeszukiwane serwisy").pack(fill="x")
        self.v_zrodla = {}
        opisy = {"pracuj.pl": "pracuj.pl  (etaty)",
                 "LinkedIn": "LinkedIn  (etaty; pytany najwyżej co %d h)" % radar.LINKEDIN_CO_ILE_GODZIN,
                 "RocketJobs": "RocketJobs  (etaty: marketing, sprzedaż, biuro)",
                 "OLX": "OLX Praca  (ogłoszenia o pracę)",
                 "Useme": "Useme  (zlecenia freelance)",
                 "WorkConnect": "WorkConnect  (zlecenia: stałe kategorie marketing i sprzedaż)"}
        for zr in radar.ZRODLA:
            v = tk.BooleanVar()
            ttk.Checkbutton(prawo, text=opisy.get(zr, zr), variable=v,
                            style="Ust.TCheckbutton").pack(anchor="w", pady=(4, 0))
            self.v_zrodla[zr] = v

        self._naglowek(prawo, "Nowa osoba?").pack(fill="x", pady=(28, 0))
        self._napis(prawo, "Jeśli dostałeś aplikację od kogoś innego, na liście są jego oferty i "
                           "statusy. Ustaw swoje frazy i filtry, zapisz, a potem wyczyść listę.",
                    slaby=True, wraplength=380).pack(fill="x", pady=(2, 8))
        self._przycisk(prawo, "Usuń wszystkie oferty i zacznij od zera…",
                       self.app.zacznij_od_zera).pack(anchor="w")

    # --- wczytywanie / zapis -------------------------------------------------
    def wczytaj(self, u):
        u = radar.uzupelnij_ustawienia(json.loads(json.dumps(u)))   # kopia
        self.frazy = [dict({"fraza": f["fraza"], "kategoria": f.get("kategoria") or "Inne",
                            "zrodla": list(f.get("zrodla") or [])},
                           **({"z_cv": True} if f.get("z_cv") else {}))   # z CV, niezmieniana
                      for f in u["frazy"]]
        self._rysuj_frazy()
        self._pole_z_listy(self.t_stop, u["stop_tytul"])
        self._pole_z_listy(self.t_jezyki, u["stop_jezyk"])
        for k, v in self.v_poziomy.items():
            v.set(k in u["poziomy"])
        self.v_miasto.set(u["miasto"])
        self._pole_z_listy(self.t_okolice, u["okolice"])
        self.v_zdalne.set(u["zdalne"])
        for zr, v in self.v_zrodla.items():
            v.set(u["zrodla"].get(zr, False))

    def zbierz(self):
        return {
            "frazy": self.frazy,
            "zrodla": {zr: v.get() for zr, v in self.v_zrodla.items()},
            "miasto": " ".join(self.v_miasto.get().split()),
            "okolice": self._lista_z_pola(self.t_okolice),
            "zdalne": self.v_zdalne.get(),
            "stop_tytul": self._lista_z_pola(self.t_stop),
            "stop_jezyk": self._lista_z_pola(self.t_jezyki),
            "poziomy": [k for k, v in self.v_poziomy.items() if v.get()],
            "cv_klucz": self.app.ustawienia.get("cv_klucz", ""),
        }

    def zapisz(self):
        u = self.zbierz()
        if not any(u["zrodla"].values()):
            messagebox.showinfo("Brak serwisów", "Zaznacz przynajmniej jeden serwis do przeszukiwania.",
                                parent=self)
            return
        if not u["frazy"] and not u["zrodla"].get("WorkConnect"):
            if not messagebox.askyesno("Brak fraz", "Lista fraz jest pusta — aplikacja nie znajdzie "
                                       "żadnych ofert. Zapisać mimo to?", parent=self):
                return
        if not u["poziomy"]:
            if not messagebox.askyesno("Brak poziomów", "Nie zaznaczono żadnego poziomu stanowiska — "
                                       "oferty z pracuj.pl z podanym poziomem będą pomijane. "
                                       "Zapisać mimo to?", parent=self):
                return
        if self.app.zapisz_ustawienia(u):
            self.destroy()

    def domyslne(self):
        if messagebox.askyesno("Przywrócić domyślne?", "Formularz wróci do ustawień domyślnych: "
                               "bez fraz i bez filtrów, oferty z całej Polski. Nic się nie zapisze, "
                               "dopóki nie klikniesz „Zapisz”.", parent=self):
            self.wczytaj(radar.domyslne_ustawienia())

    def anuluj(self):
        self.destroy()


class OknoFiltrow(tk.Toplevel):
    """Pytanie przed pobraniem ofert, gdy nie ustawiono zadnych filtrow."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.wybor = None
        self.title("Zanim pobiorę oferty")
        self.configure(bg=KOLORY["karta"])
        self.resizable(False, False)
        self.transient(app.root)
        ramka = tk.Frame(self, bg=KOLORY["karta"], padx=24, pady=20)
        ramka.pack(fill="both", expand=True)
        tk.Label(ramka, text="Dodaj filtry, żeby znaleźć najlepsze oferty", bg=KOLORY["karta"],
                 fg=KOLORY["tekst"], font=app.f_oferta, anchor="w").pack(fill="x")
        tk.Label(ramka, justify="left", anchor="w", bg=KOLORY["karta"], fg=KOLORY["tekst"],
                 font=app.f_zwykly, wraplength=540, text=(
                     "Nie ustawiono jeszcze żadnych filtrów, więc pobiorę wszystkie oferty "
                     "pasujące do fraz — także te, które do Ciebie nie pasują.\n\n"
                     "Najlepsze oferty znajdziesz, jeśli ustawisz:\n"
                     "  •  słowa, przy których oferta ma być pomijana (np. senior, kierownik),\n"
                     "  •  poziomy stanowisk, które Cię interesują,\n"
                     "  •  języki, których nie znasz,\n"
                     "  •  miasto (zakładka „Lokalizacja i źródła”).")).pack(fill="x", pady=(10, 18))
        przyciski = tk.Frame(ramka, bg=KOLORY["karta"])
        przyciski.pack(fill="x")
        b = OknoUstawien._przycisk(self, przyciski, "Przejdź do filtrów", lambda: self._wybierz("filtry"),
                                   glowny=True)
        b.pack(side="right")
        OknoUstawien._przycisk(self, przyciski, "Pomiń filtry", lambda: self._wybierz("pomin")).pack(
            side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._wybierz("filtry"))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()                 # na srodku glownego okna
        x = app.root.winfo_rootx() + (app.root.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.root.winfo_rooty() + (app.root.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))
        self.grab_set()
        self.focus_set()

    def _wybierz(self, wybor):
        self.wybor = wybor
        self.destroy()


class OknoCV(tk.Toplevel):
    """Okno "Moje CV": tresc CV (z pliku albo wklejona) i lista umiejetnosci."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Moje CV — Radar Karier")
        self.configure(bg=KOLORY["tlo"])
        self.geometry("960x660")
        self.minsize(780, 520)
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.plik = (app.cv or {}).get("plik", "")

        dol = tk.Frame(self, bg=KOLORY["tlo"])
        dol.pack(side="bottom", fill="x", padx=16, pady=(8, 14))
        self._przycisk(dol, "Zapisz", self.zapisz, glowny=True).pack(side="right")
        self._przycisk(dol, "Anuluj", self.destroy).pack(side="right", padx=(0, 8))
        if app.cv:
            self._przycisk(dol, "Usuń CV", self.usun).pack(side="left")
        tk.Label(dol, text="CV zostaje na tym komputerze (w pliku radar.db) - nic nie jest "
                           "wysyłane do internetu.", bg=KOLORY["tlo"], fg=KOLORY["slaby"],
                 font=app.f_maly).pack(side="left", padx=(12, 0))

        tresc = tk.Frame(self, bg=KOLORY["karta"], highlightthickness=1,
                         highlightbackground=KOLORY["linia"], padx=16, pady=14)
        tresc.pack(fill="both", expand=True, padx=16, pady=(14, 0))
        tresc.columnconfigure(0, weight=3)
        tresc.columnconfigure(1, weight=2)
        tresc.rowconfigure(2, weight=1)

        # lewa strona: tresc CV
        self._naglowek(tresc, "Treść CV").grid(row=0, column=0, sticky="w")
        gora = tk.Frame(tresc, bg=KOLORY["karta"])
        gora.grid(row=1, column=0, sticky="we", pady=(4, 6), padx=(0, 20))
        self._przycisk(gora, "Wczytaj z pliku (PDF, DOCX, ODT, TXT)…", self.wczytaj_plik,
                       glowny=True).pack(anchor="w")
        self.l_plik = tk.Label(gora, text="", bg=KOLORY["karta"], fg=KOLORY["slaby"],
                               font=app.f_maly, anchor="w", justify="left")
        self.l_plik.pack(anchor="w", pady=(4, 0), fill="x")
        gora.bind("<Configure>", lambda e: self.l_plik.configure(wraplength=max(200, e.width - 10)))
        ramka = tk.Frame(tresc, bg=KOLORY["karta"])
        ramka.grid(row=2, column=0, sticky="nsew", padx=(0, 20))
        self.t_cv = tk.Text(ramka, wrap="word", font=app.f_maly, relief="flat", bg=KOLORY["pole"],
                            highlightthickness=1, highlightbackground=KOLORY["linia"],
                            highlightcolor=KOLORY["akcent"], padx=8, pady=6, undo=True)
        pas = ttk.Scrollbar(ramka, orient="vertical", command=self.t_cv.yview)
        self.t_cv.configure(yscrollcommand=pas.set)
        pas.pack(side="right", fill="y")
        self.t_cv.pack(side="left", fill="both", expand=True)
        tk.Label(tresc, text="Możesz też wkleić tekst CV (Ctrl+V) albo poprawić to, co się wczytało.",
                 bg=KOLORY["karta"], fg=KOLORY["slaby"], font=app.f_maly, anchor="w").grid(
            row=3, column=0, sticky="w", pady=(6, 0))

        # prawa strona: umiejetnosci
        self._naglowek(tresc, "Twoje umiejętności").grid(row=0, column=1, sticky="w")
        tk.Label(tresc, text="Z nich liczy się dopasowanie. Jedna w linii - dopisz, czego brakuje "
                             "(np. „Allegro”), usuń, czego nie chcesz.",
                 bg=KOLORY["karta"], fg=KOLORY["slaby"], font=app.f_maly, anchor="nw",
                 justify="left", wraplength=260).grid(row=1, column=1, sticky="nwe", pady=(4, 6))
        ramka = tk.Frame(tresc, bg=KOLORY["karta"])
        ramka.grid(row=2, column=1, sticky="nsew")
        self.t_um = tk.Text(ramka, wrap="none", font=app.f_zwykly, relief="flat", bg=KOLORY["pole"],
                            highlightthickness=1, highlightbackground=KOLORY["linia"],
                            highlightcolor=KOLORY["akcent"], padx=8, pady=6, undo=True, width=28)
        pas = ttk.Scrollbar(ramka, orient="vertical", command=self.t_um.yview)
        self.t_um.configure(yscrollcommand=pas.set)
        pas.pack(side="right", fill="y")
        self.t_um.pack(side="left", fill="both", expand=True)
        self._przycisk(tresc, "Wykryj umiejętności z treści CV", self.wykryj).grid(
            row=3, column=1, sticky="w", pady=(6, 0))

        cv = app.cv or {}
        self.t_cv.insert("1.0", cv.get("tekst", ""))
        self.t_um.insert("1.0", "\n".join(cv.get("umiejetnosci", [])))
        if self.plik:
            self.l_plik.configure(text="Wczytano: %s" % self.plik)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _przycisk(self, master, tekst, komenda, glowny=False):
        return OknoUstawien._przycisk(self, master, tekst, komenda, glowny)

    def _naglowek(self, master, tekst):
        return tk.Label(master, text=tekst, bg=KOLORY["karta"], fg=KOLORY["tekst"],
                        font=self.app.f_sekcja, anchor="w")

    def _umiejetnosci(self):
        return OknoUstawien._lista_z_pola(self.t_um)

    def wczytaj_plik(self):
        sciezka = filedialog.askopenfilename(
            parent=self, title="Wybierz plik z CV",
            filetypes=[("CV (PDF, Word, OpenOffice, tekst)", "*.pdf *.docx *.odt *.txt"),
                       ("PDF", "*.pdf"), ("Word", "*.docx"), ("Wszystkie pliki", "*.*")])
        if not sciezka:
            return
        try:
            tekst = dopasowanie.wczytaj_cv(sciezka)
        except dopasowanie.BladCV as e:
            messagebox.showwarning("Nie udało się wczytać CV", str(e)[:1].upper() + str(e)[1:] + ".",
                                   parent=self)
            return
        except Exception as e:
            LOG.exception("Wczytywanie CV nie wyszlo: %s", sciezka)
            messagebox.showwarning("Nie udało się wczytać CV",
                                   "Nie udało się odczytać pliku (%s).\n\nOtwórz CV, zaznacz wszystko "
                                   "(Ctrl+A), skopiuj i wklej tutaj." % e, parent=self)
            return
        self.plik = os.path.basename(sciezka)
        self.t_cv.delete("1.0", "end")
        self.t_cv.insert("1.0", tekst)
        nowe = self.wykryj(cicho=True)
        self.l_plik.configure(text="Wczytano: %s — %d znaków, wykryto %d umiejętności. "
                                   "Sprawdź tekst i listę obok." % (self.plik, len(tekst), nowe))

    def wykryj(self, cicho=False):
        """Dopisuje do listy umiejetnosci te, ktore widac w tresci CV."""
        obecne = self._umiejetnosci()
        znalezione = dopasowanie.wykryj_umiejetnosci(self.t_cv.get("1.0", "end"), obecne)
        kanoniczne = {dopasowanie.kanoniczna(u).lower() for u in obecne}
        nowe = [u for u in znalezione if u.lower() not in kanoniczne]
        if nowe:
            tekst = self.t_um.get("1.0", "end").rstrip("\n")
            self.t_um.delete("1.0", "end")
            self.t_um.insert("1.0", (tekst + "\n" if tekst else "") + "\n".join(nowe))
        if not cicho:
            messagebox.showinfo("Umiejętności", "Dopisano %d nowych umiejętności." % len(nowe)
                                if nowe else "Nie znalazłem nic nowego - lista jest aktualna.",
                                parent=self)
        return len(nowe)

    def zapisz(self):
        tekst = self.t_cv.get("1.0", "end").strip()
        umiejetnosci = self._umiejetnosci()
        if not tekst and not umiejetnosci:
            messagebox.showinfo("Puste CV", "Wczytaj plik z CV albo wklej jego treść.", parent=self)
            return
        if tekst and not umiejetnosci:
            self.wykryj(cicho=True)
            umiejetnosci = self._umiejetnosci()
        if self.app.zapisz_cv({"tekst": tekst, "umiejetnosci": umiejetnosci, "plik": self.plik}):
            self.destroy()

    def usun(self):
        if messagebox.askyesno("Usunąć CV?", "CV i lista umiejętności zostaną usunięte, "
                               "a oferty stracą wynik dopasowania.", parent=self,
                               icon="warning", default="no"):
            if self.app.zapisz_cv(None):
                self.destroy()


def main():
    root = tk.Tk()

    def zglos_blad(exc, val, tb):
        LOG.error("".join(traceback.format_exception(exc, val, tb)))
        messagebox.showerror("Błąd", "Coś poszło nie tak:\n\n%s\n\nSzczegóły w pliku radar.log." % val)
    root.report_callback_exception = zglos_blad

    try:
        Aplikacja(root)
    except Exception as e:
        LOG.exception("Start nie wyszedl")
        messagebox.showerror("Radar Karier", "Nie udało się uruchomić aplikacji:\n\n%s" % e)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
