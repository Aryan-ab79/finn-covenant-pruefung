"""Misst die Zuverlässigkeit der LLM-Extraktion gegen die von Hand geprüfte Referenz.

    python -m finn.eval --laeufe 5

Jeder Lauf liest alle vier Verträge neu (ohne Cache) und vergleicht alle Felder,
die das Ergebnis beeinflussen: Covenant ja/nein, Typ, Operator, Grenzwert, Rundung, Parameter
– sowie den daraus gerechneten Wert für Plan 2027.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .extraktion import extrahiere_alle
from .rechnen import Covenant, pruefe
from .wirtschaftsplan import lade_plan

BASIS = Path(__file__).parent.parent / "data"


def vergleiche(auswertungen, referenz: dict, plan) -> list[str]:
    fehler = []
    ref = {v["datei"]: v for v in referenz["vertraege"]}
    for a in auswertungen:
        r = ref.get(a.datei)
        if r is None:
            continue
        if bool(a.roh.get("hat_financial_covenants")) != r["hat_financial_covenants"]:
            fehler.append(f"{a.datei}: Covenant ja/nein falsch")
        soll = {(c["typ"]): c for c in r["covenants"]}
        ist = {c.typ: c for c in a.covenants}
        if set(soll) != set(ist):
            fehler.append(f"{a.datei}: Kennzahlen {sorted(ist)} statt {sorted(soll)}")
        for typ in set(soll) & set(ist):
            s, i = soll[typ], ist[typ]
            for feld in ("operator", "grenzwert", "rundung_stellen"):
                if s[feld] != getattr(i, feld):
                    fehler.append(f"{a.datei} {typ}: {feld} {getattr(i, feld)} statt {s[feld]}")
            for k, v in s["parameter"].items():
                if i.parameter.get(k, None) != v:
                    fehler.append(f"{a.datei} {typ}: {k}={i.parameter.get(k)} statt {v}")
            w_soll = pruefe(plan, Covenant(vertrag="", **s)).wert
            w_ist = pruefe(plan, i).wert
            if abs(w_soll - w_ist) > 1e-9:
                fehler.append(f"{a.datei} {typ}: Wert {w_ist} statt {w_soll}")
        if a.zitate_ok < a.zitate_geprueft:
            fehler.append(f"{a.datei}: {a.zitate_geprueft - a.zitate_ok} Zitat(e) nicht belegt")
    return fehler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--laeufe", type=int, default=3)
    ap.add_argument("--modell", default=None)
    args = ap.parse_args()

    referenz = json.loads((BASIS / "covenants_referenz.json").read_text(encoding="utf-8"))
    plan = lade_plan(BASIS / "Wirtschaftsplan_2027.xlsx")
    fehlerfrei = 0
    for n in range(1, args.laeufe + 1):
        auswertungen = extrahiere_alle(BASIS / "vertraege", cache=False, modell=args.modell)
        fehler = vergleiche(auswertungen, referenz, plan)
        zit = sum(a.zitate_ok for a in auswertungen), sum(a.zitate_geprueft for a in auswertungen)
        print(f"Lauf {n}: {'OK' if not fehler else f'{len(fehler)} Abweichung(en)'} · Zitate belegt {zit[0]}/{zit[1]}")
        for f in fehler:
            print("   -", f)
        fehlerfrei += not fehler
    print(f"\nErgebnis: {fehlerfrei}/{args.laeufe} Läufe vollständig korrekt")


if __name__ == "__main__":
    main()
