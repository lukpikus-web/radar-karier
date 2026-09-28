// Radar Karier - maly generator kodow QR (tryb bajtowy, korekcja M, wersje 1-6).
// Bez bibliotek z internetu: kod QR z adresem aplikacji rysuje sie na miejscu.
"use strict";

const QR = (() => {
  const ECC_NA_BLOK = [0, 10, 16, 26, 18, 24, 16];   // poziom M, wersje 1-6
  const BLOKI = [0, 1, 1, 1, 2, 2, 4];

  function mnoz(x, y) {                                // mnozenie w GF(256)
    let z = 0;
    for (let i = 7; i >= 0; i--) {
      z = (z << 1) ^ ((z >>> 7) * 0x11d);
      z ^= ((y >>> i) & 1) * x;
    }
    return z;
  }
  function dzielnik(stopien) {
    const w = new Array(stopien).fill(0);
    w[stopien - 1] = 1;
    let pierw = 1;
    for (let i = 0; i < stopien; i++) {
      for (let j = 0; j < w.length; j++) {
        w[j] = mnoz(w[j], pierw);
        if (j + 1 < w.length) w[j] ^= w[j + 1];
      }
      pierw = mnoz(pierw, 2);
    }
    return w;
  }
  function reszta(dane, dziel) {
    const w = new Array(dziel.length).fill(0);
    for (const b of dane) {
      const f = b ^ w.shift();
      w.push(0);
      dziel.forEach((c, i) => (w[i] ^= mnoz(c, f)));
    }
    return w;
  }
  function surowe(ver) {                               // liczba modulow na dane
    let w = (16 * ver + 128) * ver + 64;
    if (ver >= 2) {
      const n = Math.floor(ver / 7) + 2;
      w -= (25 * n - 10) * n - 55;
    }
    return w;
  }
  const slowDanych = (ver) => Math.floor(surowe(ver) / 8) - ECC_NA_BLOK[ver] * BLOKI[ver];

  function pozycjeWyrownania(ver, rozmiar) {
    if (ver === 1) return [];
    const n = Math.floor(ver / 7) + 2;
    const krok = Math.floor((ver * 8 + n * 3 + 5) / (n * 4 - 4)) * 2;
    const w = [6];
    for (let i = 0, p = rozmiar - 7; i < n - 1; i++, p -= krok) w.splice(1, 0, p);
    return w;
  }

  function koduj(tekst) {
    const bajty = [...new TextEncoder().encode(tekst)];
    let ver = 1;
    while (ver <= 6 && 4 + 8 + bajty.length * 8 > slowDanych(ver) * 8) ver++;
    if (ver > 6) throw new Error("tekst za długi na kod QR");
    const pojemnosc = slowDanych(ver) * 8;

    // bity: tryb bajtowy, dlugosc, dane, zakonczenie, dopelnienie
    const bity = [];
    const dodaj = (v, n) => { for (let i = n - 1; i >= 0; i--) bity.push((v >>> i) & 1); };
    dodaj(4, 4);
    dodaj(bajty.length, 8);
    bajty.forEach((b) => dodaj(b, 8));
    dodaj(0, Math.min(4, pojemnosc - bity.length));
    dodaj(0, (8 - (bity.length % 8)) % 8);
    for (let p = 0xec; bity.length < pojemnosc; p ^= 0xec ^ 0x11) dodaj(p, 8);
    const dane = [];
    for (let i = 0; i < bity.length; i += 8) dane.push(parseInt(bity.slice(i, i + 8).join(""), 2));

    // bloki + korekcja bledow, przeplatane
    const nBlokow = BLOKI[ver], ecc = ECC_NA_BLOK[ver], wszystkie = Math.floor(surowe(ver) / 8);
    const krotkich = nBlokow - (wszystkie % nBlokow), dlKrotkiego = Math.floor(wszystkie / nBlokow);
    const dz = dzielnik(ecc), bloki = [];
    for (let i = 0, k = 0; i < nBlokow; i++) {
      const d = dane.slice(k, k + dlKrotkiego - ecc + (i < krotkich ? 0 : 1));
      k += d.length;
      const r = reszta(d, dz);
      if (i < krotkich) d.push(0);
      bloki.push(d.concat(r));
    }
    const slowa = [];
    for (let i = 0; i < bloki[0].length; i++)
      bloki.forEach((b, j) => { if (i !== dlKrotkiego - ecc || j >= krotkich) slowa.push(b[i]); });

    // matryca
    const N = ver * 4 + 17;
    const m = Array.from({ length: N }, () => new Array(N).fill(false));
    const funkcja = Array.from({ length: N }, () => new Array(N).fill(false));
    const ustaw = (x, y, c) => { m[y][x] = c; funkcja[y][x] = true; };
    for (let i = 0; i < N; i++) { ustaw(6, i, i % 2 === 0); ustaw(i, 6, i % 2 === 0); }
    for (const [cx, cy] of [[3, 3], [N - 4, 3], [3, N - 4]])
      for (let dy = -4; dy <= 4; dy++)
        for (let dx = -4; dx <= 4; dx++) {
          const d = Math.max(Math.abs(dx), Math.abs(dy)), x = cx + dx, y = cy + dy;
          if (x >= 0 && x < N && y >= 0 && y < N) ustaw(x, y, d !== 2 && d !== 4);
        }
    const al = pozycjeWyrownania(ver, N), ost = al.length - 1;
    al.forEach((ax, i) => al.forEach((ay, j) => {
      if ((i === 0 && j === 0) || (i === 0 && j === ost) || (i === ost && j === 0)) return;
      for (let dy = -2; dy <= 2; dy++)
        for (let dx = -2; dx <= 2; dx++) ustaw(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }));
    const formatBity = (maska) => {
      const d = (0 << 3) | maska;                      // poziom M = 00
      let r = d;
      for (let i = 0; i < 10; i++) r = (r << 1) ^ ((r >>> 9) * 0x537);
      const b = ((d << 10) | r) ^ 0x5412;
      const bit = (i) => ((b >>> i) & 1) === 1;
      for (let i = 0; i <= 5; i++) ustaw(8, i, bit(i));
      ustaw(8, 7, bit(6)); ustaw(8, 8, bit(7)); ustaw(7, 8, bit(8));
      for (let i = 9; i < 15; i++) ustaw(14 - i, 8, bit(i));
      for (let i = 0; i < 8; i++) ustaw(N - 1 - i, 8, bit(i));
      for (let i = 8; i < 15; i++) ustaw(8, N - 15 + i, bit(i));
      ustaw(8, N - 8, true);
    };
    formatBity(0);                                     // rezerwuje miejsce

    let i = 0;                                         // dane zygzakiem od prawego dolnego rogu
    for (let prawa = N - 1; prawa >= 1; prawa -= 2) {
      if (prawa === 6) prawa = 5;
      for (let v = 0; v < N; v++)
        for (let j = 0; j < 2; j++) {
          const x = prawa - j, wGore = ((prawa + 1) & 2) === 0, y = wGore ? N - 1 - v : v;
          if (!funkcja[y][x] && i < slowa.length * 8) {
            m[y][x] = ((slowa[i >>> 3] >>> (7 - (i & 7))) & 1) === 1;
            i++;
          }
        }
    }

    const MASKI = [
      (x, y) => (x + y) % 2 === 0, (x, y) => y % 2 === 0, (x) => x % 3 === 0, (x, y) => (x + y) % 3 === 0,
      (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0, (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
      (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0, (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
    ];
    const naloz = (k) => {
      for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (!funkcja[y][x] && MASKI[k](x, y)) m[y][x] = !m[y][x];
    };
    const kara = () => {                               // uproszczona ocena czytelnosci
      let k = 0, ciemne = 0;
      for (let a = 0; a < N; a++) {
        let dlW = 1, dlK = 1;
        for (let b = 1; b < N; b++) {
          if (m[a][b] === m[a][b - 1]) dlW++; else { if (dlW >= 5) k += dlW - 2; dlW = 1; }
          if (m[b][a] === m[b - 1][a]) dlK++; else { if (dlK >= 5) k += dlK - 2; dlK = 1; }
        }
        if (dlW >= 5) k += dlW - 2;
        if (dlK >= 5) k += dlK - 2;
      }
      for (let y = 0; y < N; y++)
        for (let x = 0; x < N; x++) {
          if (m[y][x]) ciemne++;
          if (x < N - 1 && y < N - 1 && m[y][x] === m[y][x + 1] && m[y][x] === m[y + 1][x] && m[y][x] === m[y + 1][x + 1]) k += 3;
        }
      return k + Math.floor(Math.abs(ciemne * 20 - N * N * 10) / (N * N)) * 10;
    };
    let najlepsza = 0, najmniej = Infinity;
    for (let k = 0; k < 8; k++) {
      naloz(k); formatBity(k);
      const p = kara();
      if (p < najmniej) { najmniej = p; najlepsza = k; }
      naloz(k);                                        // cofniecie (XOR)
    }
    naloz(najlepsza); formatBity(najlepsza);
    return m;
  }

  function svg(tekst, modul = 6) {
    const m = koduj(tekst), N = m.length, r = 4;      // 4 moduly marginesu
    let d = "";
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (m[y][x]) d += `M${x + r},${y + r}h1v1h-1z`;
    const w = (N + 2 * r) * modul;
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${w}" viewBox="0 0 ${N + 2 * r} ${N + 2 * r}" shape-rendering="crispEdges">` +
           `<rect width="100%" height="100%" fill="#fff"/><path d="${d}" fill="#000"/></svg>`;
  }

  return { koduj, svg };
})();
