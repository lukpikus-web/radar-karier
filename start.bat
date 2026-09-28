@echo off
cd /d "%~dp0"

rem Uruchamia Radar Karier w osobnym oknie, bez czarnej konsoli.

set PYW=
where pythonw >nul 2>nul && set PYW=pythonw
if "%PYW%"=="" (where pyw >nul 2>nul && set PYW=pyw)
if "%PYW%"=="" goto brak

start "" %PYW% "Radar Karier.pyw"
exit /b 0

:brak
echo.
echo  Nie znalazlem Pythona na tym komputerze.
echo  Pobierz go ze strony python.org i podczas instalacji
echo  zaznacz opcje "Add Python to PATH".
echo.
pause
exit /b 1

:brak_tk
echo.
echo  Python jest, ale bez modulu okienek (tkinter).
echo  Uruchom instalator Pythona jeszcze raz, wybierz "Modify"
echo  i zaznacz "tcl/tk and IDLE".
echo.
pause
exit /b 1
