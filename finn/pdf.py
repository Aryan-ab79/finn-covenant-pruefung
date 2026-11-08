"""PDF seitenweise lesen und Zitate gegen den Originaltext prüfen."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pdfplumber

_FUSSZEILE = re.compile(r"(Seite \d+ von \d+|Synthetisches Beispiel)", re.I)


@lru_cache(maxsize=16)
def lese_seiten(pfad: str) -> tuple[str, ...]:
    """Text je Seite (Index 0 = Seite 1), ohne Fußzeilen."""
    seiten = []
    with pdfplumber.open(pfad) as pdf:
        for page in pdf.pages:
            zeilen = (page.extract_text() or "").splitlines()
            seiten.append("\n".join(z for z in zeilen if not _FUSSZEILE.search(z)))
    return tuple(seiten)


def als_prompt_text(seiten: tuple[str, ...]) -> str:
    return "\n\n".join(f"=== Seite {i} ===\n{t}" for i, t in enumerate(seiten, 1))


def _tokens(text: str) -> list[str]:
    text = text.replace("­", "")                  # weiches Trennzeichen
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)  # Silbentrennung am Zeilenende
    return re.findall(r"[\wäöüß]+(?:[.,]\d+)*|%|§", text.lower())


@dataclass
class Fundstelle:
    gefunden: bool
    seite: int | None        # vom Code ermittelt, nicht vom LLM
    seite_laut_llm: int | None


def finde_zitat(seiten: tuple[str, ...], zitat: str, seite_laut_llm: int | None = None) -> Fundstelle:
    """Prüft, ob das Zitat (Wortfolge) wörtlich im Vertrag steht.
    Zitate über einen Seitenumbruch werden auf der Startseite gefunden."""
    q = _tokens(zitat)
    if len(q) < 3:
        return Fundstelle(False, None, seite_laut_llm)
    reihenfolge = list(range(len(seiten)))
    if seite_laut_llm and 1 <= seite_laut_llm <= len(seiten):  # angegebene Seite zuerst
        reihenfolge.remove(seite_laut_llm - 1)
        reihenfolge.insert(0, seite_laut_llm - 1)
    for i in reihenfolge:
        # Seite plus Anfang der Folgeseite, damit Zitate über einen Umbruch gefunden werden
        eigene = _tokens(seiten[i])
        t = eigene + (_tokens(seiten[i + 1][:600]) if i + 1 < len(seiten) else [])
        start = _position(t, q)
        if start is not None and start < len(eigene):  # Zitat beginnt auf dieser Seite
            return Fundstelle(True, i + 1, seite_laut_llm)
    return Fundstelle(False, None, seite_laut_llm)


def _position(heu: list[str], nadel: list[str]) -> int | None:
    n = len(nadel)
    for i in range(len(heu) - n + 1):
        if heu[i] == nadel[0] and heu[i:i + n] == nadel:
            return i
    return None


def zahl_im_text(zahl: float, text: str) -> bool:
    """Steht der Grenzwert (deutsch formatiert) im Zitat? z. B. 27.5 → '27,5', 1.3 → '1,30'."""
    kandidaten = {f"{zahl:.1f}".replace(".", ","), f"{zahl:.2f}".replace(".", ","),
                  f"{zahl:g}".replace(".", ",")}
    return any(k in _tokens(text) for k in kandidaten)
