# -*- coding: utf-8 -*-
"""
Radar Karier - uruchamianie (dwuklik w ten plik).

Otwiera okno aplikacji w Microsoft Edge (albo Chrome) w trybie aplikacji -
osobne okno bez paska adresu, rysowane przez karte graficzna.
Dane: radar.db w tym folderze. Dziennik: radar.log.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import serwer  # noqa: E402

serwer.main()
