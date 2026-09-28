// Radar Karier - okno aplikacji. Dane trzyma serwer (serwer.py) w radar.db.
"use strict";

const KLUCZ = location.hash.slice(1);
const STATUSY = [["nowa", "Nowa"], ["do_zrobienia", "Do aplikowania"], ["zaaplikowana", "Zaaplikowana"],
                 ["odrzucona", "Odrzucona"], ["nieciekawa", "Nieciekawa"]];
const NAZWA_STATUSU = Object.fromEntries(STATUSY);
const WIDOKI = [["warszawa", "Tylko Warszawa"], ["zdalne", "Tylko zdalne"], ["ukryj", "Ukryj wygasłe"],
                ["nowe", "Tylko nowe"], ["dopasowane", "Dopasowane do CV (50%+)"]];
const SORTOWANIE = [["data", "Najnowsze"], ["cv", "Najlepiej dopasowane do CV"]];
const PROG_DOPASOWANIA = 50;
const PORCJA = 40;                     // tyle kart dorysowujemy naraz (przy przewijaniu kolejne)
const OPISY_ZRODEL = {
  "pracuj.pl": "pracuj.pl (etaty)", "LinkedIn": "LinkedIn (etaty; pytany najwyżej co %d h)",
  "RocketJobs": "RocketJobs (etaty: marketing, sprzedaż, biuro)", "OLX": "OLX Praca (ogłoszenia o pracę)",
  "Useme": "Useme (zlecenia freelance)", "WorkConnect": "WorkConnect (zlecenia: stałe kategorie marketing i sprzedaż)",
};

let S = null;                          // stan z serwera
let widoczne = [];                     // oferty po filtrach
let narysowane = 0;
const f = { zrodla: new Set(), kategorie: new Set(), statusy: new Set(), widok: new Set(["ukryj"]), sort: "data" };
const rozwiniete = new Set();
let odlozone = false;                  // przeladowanie czeka, az skonczysz pisac notatke
let menuId = null;

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uprosc = (t) => String(t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ł/g, "l");

// ---------------------------------------------------------------- serwer
async function api(polecenie, dane) {
  const odp = await fetch("/api/" + polecenie, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Klucz": KLUCZ },
    body: JSON.stringify(dane || {}),
  });
  const wynik = await odp.json().catch(() => ({}));
  if (!odp.ok) throw new Error(wynik.blad || ("błąd " + odp.status));
  return wynik;
}
async function pobierzStan() {
  const odp = await fetch("/api/stan", { headers: { "X-Klucz": KLUCZ } });
  if (!odp.ok) throw new Error("brak dostępu - uruchom aplikację ponownie z pliku Radar Karier.pyw");
  S = await odp.json();
}
function stopka(tekst, ok) {
  const el = $("#stopka");
  el.textContent = tekst;
  el.classList.toggle("ok", !!ok);
}

// ---------------------------------------------------------------- daty
function dniTemu(d) {
  if (!d) return "";
  const dt = new Date(d.slice(0, 10) + "T00:00:00");
  if (isNaN(dt)) return d.slice(0, 10);
  const dni = Math.round((new Date().setHours(0, 0, 0, 0) - dt) / 864e5);
  if (dni <= 0) return "dziś";
  if (dni === 1) return "wczoraj";
  if (dni < 31) return dni + " dni temu";
  return dt.toLocaleDateString("pl-PL");
}
function lokalnyCzas(iso) {
  if (!iso) return "nigdy";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
const dataOferty = (o) => o.opublikowano || (o.pierwszy_raz || "").slice(0, 10);
const czyNowa = (o) => o.pierwszy_raz && (Date.now() - new Date(o.pierwszy_raz)) < 3 * 864e5;
const skroc = (t, n) => { t = String(t || "").replace(/\s+/g, " ").trim(); return t.length <= n ? t : t.slice(0, n - 1).trimEnd() + "…"; };

// ---------------------------------------------------------------- filtry
function wczytajFiltry() {
  const z = S.filtry;
  if (!z || typeof z !== "object") return;
  for (const k of ["zrodla", "kategorie", "statusy", "widok"]) if (Array.isArray(z[k])) f[k] = new Set(z[k]);
  if (z.sort === "cv" || z.sort === "data") f.sort = z.sort;
}
let timerFiltrow = null;
function zapiszFiltry() {
  clearTimeout(timerFiltrow);
  timerFiltrow = setTimeout(() => api("filtry", { filtry: {
    zrodla: [...f.zrodla], kategorie: [...f.kategorie], statusy: [...f.statusy], widok: [...f.widok], sort: f.sort,
  } }).catch(() => {}), 300);
}
const filtryDomyslne = () => !f.zrodla.size && !f.kategorie.size && !f.statusy.size && f.widok.size === 1 && f.widok.has("ukryj");

function filtruj() {
  const q = uprosc($("#szukaj").value.trim());
  const miasto = S.ustawienia.miasto;
  const wynik = S.oferty.filter((o) => {
    const status = o.status || "nowa", moja = status !== "nowa";
    if (f.widok.has("ukryj") && !o.aktywna && !moja) return false;
    if (f.widok.has("warszawa") && miasto && !o.warszawa) return false;
    if (f.widok.has("zdalne") && !o.zdalna) return false;
    if (f.widok.has("nowe") && !czyNowa(o)) return false;
    if (f.widok.has("dopasowane") && S.cv && ((S.dopasowania[o.id] || {}).wynik || 0) < PROG_DOPASOWANIA) return false;
    if (f.zrodla.size && !f.zrodla.has(o.zrodlo)) return false;
    if (f.kategorie.size && !f.kategorie.has(o.kategoria)) return false;
    if (f.statusy.size && !f.statusy.has(status)) return false;
    if (q && !uprosc([o.tytul, o.firma, o.opis, o.lokalizacja].join(" ")).includes(q)) return false;
    return true;
  });
  wynik.sort((a, b) => dataOferty(b).localeCompare(dataOferty(a)));
  if (f.sort === "cv" && S.cv) {
    const w = (o) => (S.dopasowania[o.id] || {}).wynik || 0;
    wynik.sort((a, b) => w(b) - w(a));            // sort jest stabilny: przy remisie nowsze wyzej
  }
  return wynik;
}

function rysujChipy() {
  const zrodla = [...new Set(S.oferty.map((o) => o.zrodlo).filter(Boolean))].sort();
  const kategorie = [...new Set(S.oferty.map((o) => o.kategoria).filter(Boolean))].sort();
  const miasto = S.ustawienia.miasto;
  const widoki = WIDOKI.filter(([k]) => (k !== "dopasowane" || S.cv) && (k !== "warszawa" || miasto))
                       .map(([k, n]) => [k, k === "warszawa" ? "Tylko " + miasto : n]);
  const grupy = { zrodla: zrodla.map((z) => [z, z]), kategorie: kategorie.map((k) => [k, k]),
                  statusy: STATUSY, widok: widoki, sort: SORTOWANIE };
  for (const [grupa, pozycje] of Object.entries(grupy)) {
    const wl = grupa === "sort" ? new Set([f.sort]) : f[grupa];
    let html = pozycje.map(([k, n]) => `<button class="chip${wl.has(k) ? " wl" : ""}" data-grupa="${grupa}" data-k="${esc(k)}">${esc(n)}</button>`).join("");
    if (grupa === "sort" && !S.cv) html += `<span class="podpowiedz">Dodaj CV (przycisk „Dodaj CV”), żeby zobaczyć dopasowanie ofert</span>`;
    $(`.chipy[data-grupa="${grupa}"]`).innerHTML = html;
  }
  $("#wyczysc-filtry").hidden = filtryDomyslne();
}

function licznikiRysuj() {
  const w = { aktywne: 0, nowe: 0, do_zrobienia: 0, zaaplikowana: 0, odrzucona: 0 };
  for (const o of S.oferty) {
    const st = o.status || "nowa";
    if (o.aktywna) { w.aktywne++; if (czyNowa(o) && st === "nowa") w.nowe++; }
    if (st in w) w[st]++;
  }
  const L = [["aktywne", "Aktywne oferty", ""], ["nowe", "Nowe (3 dni)", "nowe"], ["do_zrobienia", "Do aplikowania", "do_zrobienia"],
             ["zaaplikowana", "Zaaplikowane", "zaaplikowana"], ["odrzucona", "Odrzucone", "odrzucona"]];
  $("#liczniki").innerHTML = L.map(([k, n, fl]) => `<div class="licznik"${fl ? ` data-filtr="${fl}"` : ""}><b>${w[k]}</b><span>${n}</span></div>`).join("");
  $("#podtytul").textContent = `${S.oferty.length} ofert w bazie · ostatnie odświeżenie: ${lokalnyCzas(S.ostatnie_odswiezenie)}`;
  $("#b-cv").textContent = S.cv ? "Moje CV ✓" : "Dodaj CV";
}

// ---------------------------------------------------------------- karty
function etykiety(o) {
  const e = [];
  const d = S.cv ? S.dopasowania[o.id] : null;
  if (d) e.push([`Dopasowanie ${d.wstepny ? "~" : ""}${d.wynik}%`, d.wynik >= 70 ? "dop-wys" : d.wynik >= 45 ? "dop-sr" : "dop-nis"]);
  if (czyNowa(o)) e.push(["nowa", "nowa"]);
  if (!o.aktywna) e.push(["wygasła", "wygasla"]);
  if (o.zdalna) e.push(["zdalnie", "zdalnie"]);
  if (o.wynagrodzenie) e.push([skroc(o.wynagrodzenie, 50), "kasa"]);
  for (const p of ["umowa", "termin", "zrodlo", "kategoria"]) if (o[p]) e.push([skroc(o[p], 40), ""]);
  return e.map(([t, k]) => `<span class="et ${k}">${esc(t)}</span>`).join("");
}

function szczegolyHtml(o) {
  const sz = S.szczegoly[o.id];
  if (S.pobierane.includes(o.id)) return `<div class="szczegoly">Pobieram opis oferty z ${esc(o.zrodlo || "serwisu")}…</div>`;
  if (S.bledy_szczeg[o.id] || !sz) {
    return `<div class="szczegoly"><div class="blad">Nie udało się pobrać opisu: ${esc(S.bledy_szczeg[o.id] || "brak danych")}.</div>
      <button class="link" data-akcja="ponow" style="margin-top:6px">Spróbuj ponownie</button></div>`;
  }
  const sekcje = [["opis", "Opis"], ["obowiazki", "Obowiązki"], ["wymagania", "Wymagania"], ["mile_widziane", "Mile widziane"], ["oferujemy", "Oferujemy"]];
  let h = "";
  for (const [k, n] of sekcje) {
    const lista = sz[k];
    if (!lista || !lista.length) continue;
    h += `<h4>${n}</h4>` + (k === "opis" ? lista.map((t) => `<p>${esc(t)}</p>`).join("")
                                         : `<ul>${lista.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`);
  }
  h += `<div class="stopka">Z ${esc(o.zrodlo || "serwisu")}, pobrano ${lokalnyCzas(sz.pobrano)} · <button class="link" data-akcja="ponow">pobierz ponownie</button></div>`;
  return `<div class="szczegoly">${h}</div>`;
}

function kartaHtml(o) {
  const st = o.status || "nowa";
  const d = S.cv ? S.dopasowania[o.id] : null;
  const rozw = rozwiniete.has(o.id);
  let cv = "";
  if (d && (d.pasuje.length || d.brakuje.length)) {
    const lista = (l) => esc(l.slice(0, 6).join(", ") + (l.length > 6 ? " …" : ""));
    cv = `<div class="cv-linia">${d.pasuje.length ? `<span class="tak">✓ ${lista(d.pasuje)}</span>` : ""}${d.brakuje.length ? `<span class="nie">brakuje: ${lista(d.brakuje)}</span>` : ""}</div>`;
  }
  const meta = [o.firma, o.lokalizacja].filter(Boolean).join(" · ");
  const opis = o.opis && !(rozw && S.szczegoly[o.id]) ? `<p class="opis">${esc(skroc(o.opis, 230))}</p>` : "";
  return `<article class="karta s-${st}" data-id="${esc(o.id)}">
    <div class="gora">${o.url ? `<a class="tytul" href="${esc(o.url)}" data-akcja="otworz">${esc(o.tytul || "(bez tytułu)")}</a>` : `<span class="tytul">${esc(o.tytul || "(bez tytułu)")}</span>`}
      <span class="data">${esc(dniTemu(dataOferty(o)))}</span></div>
    ${meta ? `<div class="meta">${esc(meta)}</div>` : ""}
    <div class="etykiety">${etykiety(o)}</div>
    ${cv}${opis}
    ${o.url ? `<button class="link rozwin" data-akcja="rozwin">${rozw ? "▴ Zwiń szczegóły" : "▾ Obowiązki i wymagania"}</button>` : ""}
    ${rozw ? szczegolyHtml(o) : ""}
    <div class="dol">
      <div class="statusy">${STATUSY.map(([k, n]) => `<button class="st s-${k}${k === st ? " wl" : ""}" data-status="${k}">${n}</button>`).join("")}</div>
      ${o.url ? `<a class="otworz" href="${esc(o.url)}" data-akcja="otworz">Otwórz ofertę ↗</a>` : ""}
      <input class="notatka" value="${esc(o.notatka || "")}" placeholder="notatka…" data-id="${esc(o.id)}">
    </div></article>`;
}

function rysuj(zachowajPozycje) {
  const y = window.scrollY;
  const ile = zachowajPozycje ? Math.max(narysowane, PORCJA) : PORCJA;
  widoczne = filtruj();
  narysowane = 0;
  $("#lista").innerHTML = widoczne.length ? "" : `<div class="pusto">Brak ofert dla wybranych filtrów.</div>`;
  dorysuj(ile);
  rysujChipy();
  licznikiRysuj();
  $("#info-listy").textContent = widoczne.length ? `Ofert: ${widoczne.length} z ${S.oferty.length}` : "";
  if (zachowajPozycje) window.scrollTo(0, y);
}
function dorysuj(ile = PORCJA) {
  const do_ = Math.min(widoczne.length, narysowane + ile);
  if (do_ <= narysowane) return;
  $("#lista").insertAdjacentHTML("beforeend", widoczne.slice(narysowane, do_).map(kartaHtml).join(""));
  narysowane = do_;
}
// kolejne karty, gdy zblizasz sie do konca listy
new IntersectionObserver((e) => { if (e[0].isIntersecting && S) dorysuj(); }, { rootMargin: "1500px" }).observe($("#wiecej"));

function odswiezKarte(id) {
  const el = $(`.karta[data-id="${CSS.escape(id)}"]`);
  const o = S.oferty.find((x) => x.id === id);
  if (el && o) el.outerHTML = kartaHtml(o);
}

// ---------------------------------------------------------------- akcje na kartach
$("#lista").addEventListener("click", async (e) => {
  const karta = e.target.closest(".karta");
  if (!karta) return;
  const id = karta.dataset.id, o = S.oferty.find((x) => x.id === id);
  const st = e.target.closest("[data-status]");
  const akcja = e.target.closest("[data-akcja]")?.dataset.akcja;
  if (st) {
    const kod = st.dataset.status;
    try {
      await api("status", { id, status: kod });
      o.status = kod;
      odswiezKarte(id); licznikiRysuj();
      stopka(`✓ Status zapisany (${NAZWA_STATUSU[kod]}): ${skroc(o.tytul, 60)} · ${new Date().toLocaleTimeString("pl-PL")}`, true);
    } catch (err) { alert("Status NIE został zapisany.\n\n" + err.message); }
  } else if (akcja === "otworz") {
    e.preventDefault();
    otworz(o);
  } else if (akcja === "rozwin") {
    if (rozwiniete.has(id)) rozwiniete.delete(id);
    else { rozwiniete.add(id); if (!S.szczegoly[id] && !S.pobierane.includes(id)) await pobierzOpis(id); }
    odswiezKarte(id);
  } else if (akcja === "ponow") {
    await pobierzOpis(id);
    odswiezKarte(id);
  }
});
async function pobierzOpis(id) {
  try { await api("szczegoly", { id }); S.pobierane.push(id); delete S.bledy_szczeg[id]; stopka("Pobieram opis oferty…"); }
  catch (err) { S.bledy_szczeg[id] = err.message; }
}
function otworz(o) {
  // oferta otwiera sie w Twojej zwyklej przegladarce (tam jestes zalogowany)
  api("otworz", { url: o.url }).then(() => stopka("Otwarto w przeglądarce: " + o.url)).catch(() => window.open(o.url, "_blank"));
}

// notatki: zapis sam, chwile po ostatnim klawiszu
const timeryNotatek = {};
$("#lista").addEventListener("input", (e) => {
  if (!e.target.classList.contains("notatka")) return;
  const id = e.target.dataset.id, tekst = e.target.value;
  clearTimeout(timeryNotatek[id]);
  timeryNotatek[id] = setTimeout(() => zapiszNotatke(id, tekst), 700);
});
$("#lista").addEventListener("focusout", (e) => {
  if (!e.target.classList.contains("notatka")) return;
  const id = e.target.dataset.id;
  clearTimeout(timeryNotatek[id]);
  zapiszNotatke(id, e.target.value);
  if (odlozone) { odlozone = false; setTimeout(przeladuj, 50); }
});
$("#lista").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.classList.contains("notatka")) e.target.blur(); });
async function zapiszNotatke(id, tekst) {
  const o = S.oferty.find((x) => x.id === id);
  if (!o || (o.notatka || "") === tekst) return;
  try {
    await api("notatka", { id, tekst });
    o.notatka = tekst;
    stopka(`✓ Notatka zapisana: ${skroc(o.tytul, 60)} · ${new Date().toLocaleTimeString("pl-PL")}`, true);
  } catch (err) { alert("Notatka NIE została zapisana.\n\n" + err.message); }
}

// menu pod prawym przyciskiem
$("#lista").addEventListener("contextmenu", (e) => {
  const karta = e.target.closest(".karta");
  if (!karta || e.target.closest("input")) return;
  e.preventDefault();
  menuId = karta.dataset.id;
  const m = $("#menu");
  m.style.display = "block";
  m.style.left = Math.min(e.clientX, innerWidth - m.offsetWidth - 8) + "px";
  m.style.top = Math.min(e.clientY, innerHeight - m.offsetHeight - 8) + "px";
});
document.addEventListener("click", (e) => { if (!e.target.closest("#menu")) $("#menu").style.display = "none"; });
$("#menu").addEventListener("click", async (e) => {
  const co = e.target.dataset.menu, o = S.oferty.find((x) => x.id === menuId);
  $("#menu").style.display = "none";
  if (!o) return;
  if (co === "otworz") otworz(o);
  if (co === "kopiuj") { await navigator.clipboard.writeText(o.url).catch(() => {}); stopka("Skopiowano link: " + o.url, true); }
  if (co === "szczegoly") { $(`.karta[data-id="${CSS.escape(o.id)}"] [data-akcja="rozwin"]`)?.click(); }
});

// ---------------------------------------------------------------- panel filtrow
document.querySelector(".panel").addEventListener("click", (e) => {
  const c = e.target.closest(".chip");
  if (!c) return;
  const g = c.dataset.grupa, k = c.dataset.k;
  if (g === "sort") {
    if (k === "cv" && !S.cv) { pytanie("Najpierw CV", "Żeby sortować po dopasowaniu, dodaj swoje CV.", [["Dodaj CV", "cv", true], ["Anuluj", null]]).then((w) => w && otworzCV()); return; }
    f.sort = k;
  } else if (f[g].has(k)) f[g].delete(k); else f[g].add(k);
  zapiszFiltry(); rysuj();
});
$("#wyczysc-filtry").addEventListener("click", () => {
  f.zrodla.clear(); f.kategorie.clear(); f.statusy.clear(); f.widok = new Set(["ukryj"]);
  zapiszFiltry(); rysuj();
});
$("#liczniki").addEventListener("click", (e) => {
  const l = e.target.closest("[data-filtr]");
  if (!l) return;
  const k = l.dataset.filtr;
  if (k === "nowe") { f.widok.has("nowe") ? f.widok.delete("nowe") : f.widok.add("nowe"); }
  else if (f.statusy.size === 1 && f.statusy.has(k)) f.statusy.clear();
  else f.statusy = new Set([k]);
  zapiszFiltry(); rysuj();
});
let timerSzukania = null;
$("#szukaj").addEventListener("input", () => { clearTimeout(timerSzukania); timerSzukania = setTimeout(() => rysuj(), 200); });

// ---------------------------------------------------------------- okna dialogowe
function pytanie(tytul, tresc, przyciski) {
  // przyciski: [[napis, wartosc, glowny?], ...]; zamkniecie okna = null
  return new Promise((gotowe) => {
    const d = $("#d-pytanie");
    $("#p-tytul").textContent = tytul;
    $("#p-tresc").innerHTML = tresc;
    $("#p-przyciski").innerHTML = "";
    for (const [napis, wartosc, glowny] of przyciski) {
      const b = document.createElement("button");
      b.className = "btn" + (glowny ? " glowny" : "");
      b.textContent = napis;
      b.onclick = () => { d.close(); gotowe(wartosc); };
      $("#p-przyciski").append(b);
    }
    d.onclose = () => gotowe(null);
    d.showModal();
    $("#p-przyciski .glowny")?.focus();
  });
}
$$("dialog [data-zamknij]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

// ---------------------------------------------------------------- Ustawienia
let U = null;                  // edytowana kopia ustawien
function lista(tekst) {
  const wynik = [];
  for (const l of tekst.replace(/,/g, "\n").split("\n")) {
    const x = l.replace(/\s+/g, " ").trim();
    if (x && !wynik.some((w) => w.toLowerCase() === x.toLowerCase())) wynik.push(x);
  }
  return wynik;
}
function otworzUstawienia(zakladka = 0) {
  wypelnijUstawienia(JSON.parse(JSON.stringify(S.ustawienia)));
  pokazZakladke(zakladka);
  $("#d-ustawienia").showModal();
}
function pokazZakladke(n) {
  $$("#d-ustawienia .zakladka").forEach((z) => z.classList.toggle("wl", +z.dataset.zakladka === n));
  $$("#d-ustawienia [data-panel]").forEach((p) => (p.hidden = +p.dataset.panel !== n));
}
$$("#d-ustawienia .zakladka").forEach((z) => z.addEventListener("click", () => pokazZakladke(+z.dataset.zakladka)));
function wypelnijUstawienia(u) {
  U = u;
  $("#frazy-naglowek").innerHTML = `<tr><th style="width:36%">Fraza / stanowisko</th><th style="width:24%">Kategoria</th>${S.zrodla_fraz.map((z) => `<th class="srodek">${esc(z)}</th>`).join("")}<th></th></tr>`;
  rysujFrazy();
  $("#u-stop").value = u.stop_tytul.join("\n");
  $("#u-jezyki").value = u.stop_jezyk.join("\n");
  $("#u-poziomy").innerHTML = S.poziomy.map(([k, n]) => `<label class="wybor"><input type="checkbox" value="${k}"${u.poziomy.includes(k) ? " checked" : ""}> ${esc(n)}</label>`).join("");
  $("#u-miasto").value = u.miasto;
  $("#u-okolice").value = u.okolice.join("\n");
  $("#u-zdalne").checked = u.zdalne;
  $("#u-zrodla").innerHTML = S.zrodla.map((z) => `<label class="wybor"><input type="checkbox" value="${esc(z)}"${u.zrodla[z] ? " checked" : ""}> ${esc((OPISY_ZRODEL[z] || z).replace("%d", S.linkedin.co_ile_godzin))}</label>`).join("");
}
function rysujFrazy() {
  $("#frazy").innerHTML = U.frazy.map((fr, i) => `<tr data-i="${i}">
    <td><input class="pole" data-pole="fraza" value="${esc(fr.fraza)}"></td>
    <td><div class="kat"><input class="pole" data-pole="kategoria" value="${esc(fr.kategoria)}" list="kategorie-lista">${fr.z_cv ? '<span class="odznaka" title="Fraza wzięta z CV">Z CV</span>' : ""}</div></td>
    ${S.zrodla_fraz.map((z) => `<td class="srodek"><input type="checkbox" data-zrodlo="${esc(z)}"${fr.zrodla.includes(z) ? " checked" : ""}></td>`).join("")}
    <td><button type="button" class="link" data-usun title="Usuń frazę">✕</button></td></tr>`).join("") +
    (U.frazy.length ? "" : `<tr><td colspan="9" class="podpowiedz" style="padding:14px 6px">Brak fraz. Dodaj CV — frazy wezmę z niego — albo kliknij „+ Dodaj frazę”.</td></tr>`);
  const kat = [...new Set(U.frazy.map((x) => x.kategoria).filter(Boolean))];
  let dl = $("#kategorie-lista");
  if (!dl) { dl = document.createElement("datalist"); dl.id = "kategorie-lista"; document.body.append(dl); }
  dl.innerHTML = kat.map((k) => `<option value="${esc(k)}">`).join("");
  licznikFraz();
}
function licznikFraz() {
  const wL = U.frazy.filter((x) => x.zrodla.includes("LinkedIn")).length;
  const miasto = $("#u-miasto").value.trim();
  const limit = Math.floor(S.linkedin.max_zapytan / ($("#u-zdalne").checked && miasto ? 2 : 1));
  const el = $("#licznik-fraz");
  el.textContent = `${U.frazy.length} fraz · LinkedIn: ${wL} z max ${limit}${wL > limit ? " (nadmiar pominięty)" : ""}`;
  el.classList.toggle("za-duzo", wL > limit);
}
$("#frazy").addEventListener("input", (e) => {
  const tr = e.target.closest("tr[data-i]");
  if (!tr) return;
  const fr = U.frazy[+tr.dataset.i];
  if (e.target.dataset.pole) { fr[e.target.dataset.pole] = e.target.value; delete fr.z_cv; }   // zmieniona = Twoja
  if (e.target.dataset.zrodlo) {
    const z = e.target.dataset.zrodlo;
    fr.zrodla = e.target.checked ? [...new Set([...fr.zrodla, z])] : fr.zrodla.filter((x) => x !== z);
    licznikFraz();
  }
});
$("#frazy").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-i]");
  if (tr && e.target.closest("[data-usun]")) { U.frazy.splice(+tr.dataset.i, 1); rysujFrazy(); }
});
$("#dodaj-fraze").addEventListener("click", () => {
  U.frazy.unshift({ fraza: "", kategoria: "", zrodla: ["pracuj.pl", "RocketJobs", "OLX"] });
  rysujFrazy();
  $('#frazy tr[data-i="0"] input').focus();
});
$("#u-miasto").addEventListener("input", licznikFraz);
$("#u-zdalne").addEventListener("change", licznikFraz);
$("#u-domyslne").addEventListener("click", async () => {
  if (await pytanie("Przywrócić domyślne?", "Formularz wróci do ustawień domyślnych: bez fraz i bez filtrów, oferty z całej Polski. Nic się nie zapisze, dopóki nie klikniesz „Zapisz”.", [["Przywróć", 1, true], ["Anuluj", null]])) {
    const d = await api("domyslne");
    wypelnijUstawienia(d.ustawienia);
  }
});
$("#u-zapisz").addEventListener("click", async () => {
  const frazy = [];
  for (const fr of U.frazy) {
    const fraza = fr.fraza.replace(/\s+/g, " ").trim();
    if (!fraza) continue;
    if (frazy.some((x) => x.fraza.toLowerCase() === fraza.toLowerCase())) continue;
    if (!fr.zrodla.length) { pokazZakladke(0); return alert(`Fraza „${fraza}” nie ma zaznaczonego żadnego serwisu.`); }
    frazy.push({ ...fr, fraza, kategoria: fr.kategoria.replace(/\s+/g, " ").trim() || "Inne" });
  }
  const u = {
    frazy, zrodla: Object.fromEntries($$("#u-zrodla input").map((c) => [c.value, c.checked])),
    miasto: $("#u-miasto").value.replace(/\s+/g, " ").trim(), okolice: lista($("#u-okolice").value),
    zdalne: $("#u-zdalne").checked, stop_tytul: lista($("#u-stop").value), stop_jezyk: lista($("#u-jezyki").value),
    poziomy: $$("#u-poziomy input:checked").map((c) => c.value), wersja: 2, cv_klucz: S.ustawienia.cv_klucz || "",
  };
  if (!Object.values(u.zrodla).some(Boolean)) { pokazZakladke(2); return alert("Zaznacz przynajmniej jeden serwis do przeszukiwania."); }
  if (!u.frazy.length && !u.zrodla.WorkConnect && !confirm("Lista fraz jest pusta — aplikacja nie znajdzie żadnych ofert. Zapisać mimo to?")) return;
  if (!u.poziomy.length && !confirm("Nie zaznaczono żadnego poziomu stanowiska — oferty z podanym poziomem będą pomijane. Zapisać mimo to?")) return;
  try {
    const w = await api("ustawienia", { ustawienia: u });
    $("#d-ustawienia").close();
    await przeladuj();
    stopka("✓ Ustawienia zapisane · " + new Date().toLocaleTimeString("pl-PL"), true);
    const tekst = "Ustawienia zapisane." + (w.usunietych ? `<p>Usunięto ${w.usunietych} ofert, które nie pasują do nowych filtrów (oferty z Twoim statusem albo notatką zostają zawsze).</p>` : "") + "<p>Pobrać teraz oferty według nowych ustawień?</p>";
    if (await pytanie("Radar Karier", tekst, [["Pobierz oferty", 1, true], ["Nie teraz", null]])) odswiez();
  } catch (err) { alert("Ustawienia NIE zostały zapisane.\n\n" + err.message); }
});
$("#zacznij-od-zera").addEventListener("click", async () => {
  const w = await pytanie("Zacząć od zera?", "To usunie <b>WSZYSTKIE</b> oferty z bazy razem ze statusami, notatkami, pobranymi opisami i zapisanym CV. Ustawienia wyszukiwania zostaną.<p>Wcześniej zrobię kopię bazy w folderze „kopie”.</p>", [["Usuń wszystko", 1, true], ["Anuluj", null]]);
  if (!w) return;
  try {
    await api("zacznij_od_zera");
    f.zrodla.clear(); f.kategorie.clear(); f.statusy.clear(); f.widok = new Set(["ukryj"]); f.sort = "data";
    rozwiniete.clear();
    zapiszFiltry();
    await przeladuj();
  } catch (err) { alert(err.message); }
});

// ---------------------------------------------------------------- CV
function otworzCV() {
  const cv = S.cv || {};
  $("#cv-tekst").value = cv.tekst || "";
  $("#cv-umiejetnosci").value = (cv.umiejetnosci || []).join("\n");
  $("#cv-info").textContent = cv.plik ? "Wczytano: " + cv.plik : "";
  $("#cv-usun").hidden = !S.cv;
  $("#d-cv").dataset.plik = cv.plik || "";
  $("#d-cv").showModal();
}
async function wczytajPlikCV(plik) {
  if (!plik) return;
  $("#cv-info").textContent = "Wczytuję " + plik.name + "…";
  try {
    const odp = await fetch("/api/cv_plik", { method: "POST", headers: { "X-Klucz": KLUCZ, "X-Nazwa": encodeURIComponent(plik.name) }, body: plik });
    const w = await odp.json();
    if (!odp.ok) throw new Error(w.blad);
    $("#cv-tekst").value = w.tekst;
    $("#d-cv").dataset.plik = plik.name;
    const nowe = await wykryj(true);
    $("#cv-info").textContent = `Wczytano: ${plik.name} — ${w.tekst.length} znaków, wykryto ${nowe} umiejętności. Sprawdź tekst i listę obok.`;
  } catch (err) {
    $("#cv-info").textContent = "";
    alert("Nie udało się wczytać CV.\n\n" + err.message);
  }
}
async function wykryj(cicho) {
  const obecne = lista($("#cv-umiejetnosci").value);
  const w = await api("wykryj", { tekst: $("#cv-tekst").value, obecne });
  if (w.nowe.length) $("#cv-umiejetnosci").value = [...obecne, ...w.nowe].join("\n");
  if (!cicho) alert(w.nowe.length ? `Dopisano ${w.nowe.length} nowych umiejętności.` : "Nie znalazłem nic nowego — lista jest aktualna.");
  return w.nowe.length;
}
$("#cv-plik").addEventListener("change", (e) => wczytajPlikCV(e.target.files[0]));
const upusc = $("#upusc");
upusc.addEventListener("dragover", (e) => { e.preventDefault(); upusc.classList.add("nad"); });
upusc.addEventListener("dragleave", () => upusc.classList.remove("nad"));
upusc.addEventListener("drop", (e) => { e.preventDefault(); upusc.classList.remove("nad"); wczytajPlikCV(e.dataTransfer.files[0]); });
$("#cv-wykryj").addEventListener("click", () => wykryj(false));
$("#cv-zapisz").addEventListener("click", async () => {
  const tekst = $("#cv-tekst").value.trim();
  let umiejetnosci = lista($("#cv-umiejetnosci").value);
  if (!tekst && !umiejetnosci.length) return alert("Wczytaj plik z CV albo wklej jego treść.");
  if (tekst && !umiejetnosci.length) { await wykryj(true); umiejetnosci = lista($("#cv-umiejetnosci").value); }
  const pierwsze = !S.cv;
  try {
    const w = await api("cv", { cv: { tekst, umiejetnosci, plik: $("#d-cv").dataset.plik || "" } });
    $("#d-cv").close();
    if (pierwsze) { f.sort = "cv"; zapiszFiltry(); }
    await przeladuj();
    stopka("✓ CV zapisane — oferty mają teraz wynik dopasowania", true);
    if (w.dodane.length && await pytanie("Frazy z CV", `Na podstawie CV będę szukać ofert po:<ul class="lista-punktow">${w.dodane.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><p>Zmienisz je w oknie „Ustawienia” (kategoria „Z CV”) — możesz też dopisać własne.</p><p>Pobrać teraz oferty?</p>`, [["Pobierz oferty", 1, true], ["Nie teraz", null]])) odswiez();
  } catch (err) { alert("CV NIE zostało zapisane.\n\n" + err.message); }
});
$("#cv-usun").addEventListener("click", async () => {
  if (!(await pytanie("Usunąć CV?", "CV i lista umiejętności zostaną usunięte, a oferty stracą wynik dopasowania.", [["Usuń CV", 1, true], ["Anuluj", null]]))) return;
  await api("cv", { cv: null });
  $("#d-cv").close();
  f.sort = "data"; f.widok.delete("dopasowane"); zapiszFiltry();
  await przeladuj();
  stopka("CV usunięte");
});

// ---------------------------------------------------------------- pobieranie ofert
function brakFiltrow() {
  const u = S.ustawienia;
  return !u.stop_tytul.length && !u.stop_jezyk.length && !u.miasto && S.poziomy.every(([k]) => u.poziomy.includes(k));
}
async function odswiez() {
  if (S.postep.trwa) return;
  if (!S.ustawienia.frazy.length && !S.cv && !S.ustawienia.zrodla.WorkConnect) {
    const w = await pytanie("Czego szukać?", "Nie ma jeszcze żadnych stanowisk ani słów kluczowych.<p>Dodaj CV — frazy wezmę z niego — albo wpisz je w oknie „Ustawienia”.</p>", [["Dodaj CV", "cv", true], ["Otwórz Ustawienia", "u"], ["Anuluj", null]]);
    if (w === "cv") otworzCV(); else if (w === "u") otworzUstawienia(0);
    return;
  }
  if (brakFiltrow()) {
    const w = await pytanie("Dodaj filtry, żeby znaleźć najlepsze oferty",
      `<p>Nie ustawiono jeszcze żadnych filtrów, więc pobiorę wszystkie oferty pasujące do fraz — także te, które do Ciebie nie pasują.</p>
       <p>Najlepsze oferty znajdziesz, jeśli ustawisz:</p><ul class="lista-punktow">
       <li>słowa, przy których oferta ma być pomijana (np. senior, kierownik),</li><li>poziomy stanowisk, które Cię interesują,</li>
       <li>języki, których nie znasz,</li><li>miasto (zakładka „Lokalizacja i źródła”).</li></ul>`,
      [["Pomiń filtry", "pomin"], ["Przejdź do filtrów", "filtry", true]]);
    if (w === "filtry") return otworzUstawienia(1);
    if (w !== "pomin") return;
  }
  try {
    await api("odswiez");
    S.postep = { trwa: true, log: "Pobieram świeże oferty…", koniec: "" };
    pokazPostep();
  } catch (err) {
    if (err.message === "BRAK_FRAZ") return otworzUstawienia(0);
    alert(err.message);
  }
}
function pokazPostep() {
  const trwa = S.postep.trwa;
  $("#b-odswiez").disabled = trwa;
  $("#b-odswiez").textContent = trwa ? "Pobieram…" : "Odśwież oferty";
  $("#pasek").classList.toggle("widoczny", trwa);
  if (trwa) stopka(S.postep.log || "Pobieram świeże oferty…");
}

// ---------------------------------------------------------------- sygnal zycia + zmiany z serwera
let wersja = -1, pokazanyKoniec = null;
async function przeladuj() {
  await pobierzStan();
  wersja = S.wersja;
  rysuj(true);
  pokazPostep();
}
async function sprawdz() {
  try {
    const odp = await fetch("/api/wersja", { headers: { "X-Klucz": KLUCZ } });
    const w = await odp.json();
    S.postep = w.postep;
    pokazPostep();
    if (w.wersja !== wersja) {
      const piszesz = document.activeElement && document.activeElement.classList.contains("notatka");
      if (piszesz) odlozone = true;           // nie przerywamy pisania notatki
      else await przeladuj();
    }
    // podsumowanie pobierania ("Gotowe: 12 nowych...") - raz, gdy sie pojawi
    if (!S.postep.trwa && S.postep.koniec && S.postep.koniec !== pokazanyKoniec) {
      pokazanyKoniec = S.postep.koniec;
      stopka(S.postep.koniec, true);
    }
  } catch (err) {
    stopka("Brak połączenia z aplikacją — uruchom ją ponownie z pliku Radar Karier.pyw");
  }
}
setInterval(sprawdz, 1500);

// ---------------------------------------------------------------- przyciski i klawisze
$("#b-odswiez").addEventListener("click", odswiez);
$("#b-ustawienia").addEventListener("click", () => otworzUstawienia(0));
$("#b-cv").addEventListener("click", otworzCV);
document.addEventListener("keydown", (e) => {
  if (e.key === "F5") { e.preventDefault(); if (!$("dialog[open]")) odswiez(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") { e.preventDefault(); $("#szukaj").focus(); window.scrollTo(0, 0); }
  else if (e.key === "Escape" && !$("dialog[open]") && document.activeElement === $("#szukaj") && $("#szukaj").value) { $("#szukaj").value = ""; rysuj(); }
});

// ---------------------------------------------------------------- start
(async () => {
  try {
    await pobierzStan();
    wersja = S.wersja;
    pokazanyKoniec = S.postep.koniec;
    wczytajFiltry();
    rysuj();
    pokazPostep();
    stopka(S.oferty.length ? `Ofert w bazie: ${S.oferty.length} · statusy i notatki zapisują się w pliku radar.db` : "Dodaj CV albo frazy w Ustawieniach i kliknij „Odśwież oferty”.");
  } catch (err) {
    document.body.innerHTML = `<div class="strona"><h1>Radar Karier</h1><p>${esc(err.message)}</p></div>`;
  }
})();
