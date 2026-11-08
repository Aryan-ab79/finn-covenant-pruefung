"""Zahl mit Herkunft: jede Kennzahl trägt ihre Quellen bis in die Ergebnis-Mail."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class Wert:
    """Ein Betrag in T€ (oder eine Kennzahl) plus die Fundstellen, aus denen er stammt."""

    betrag: float
    quellen: tuple[str, ...] = field(default_factory=tuple)
    bezeichnung: str = ""

    def __add__(self, other: "Wert") -> "Wert":
        return Wert(self.betrag + other.betrag, self.quellen + other.quellen)

    def __sub__(self, other: "Wert") -> "Wert":
        return Wert(self.betrag - other.betrag, self.quellen + other.quellen)

    def mal(self, faktor: float, hinweis: str = "") -> "Wert":
        q = self.quellen + ((hinweis,) if hinweis else ())
        return Wert(self.betrag * faktor, q)

    def benannt(self, bezeichnung: str) -> "Wert":
        return Wert(self.betrag, self.quellen, bezeichnung)


def summe(werte: list[Wert]) -> Wert:
    gesamt = Wert(0.0)
    for w in werte:
        gesamt = gesamt + w
    return gesamt


def kaufm_runden(x: float, stellen: int) -> float:
    """Kaufmännisches Runden (0,5 → aufrunden), wie in den Verträgen verlangt.
    Pythons round() rundet zur geraden Zahl und wäre hier falsch."""
    q = Decimal(1).scaleb(-stellen)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))
