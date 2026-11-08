"""Finn starten.

    python -m finn                       Postfach dauerhaft überwachen (Live-Betrieb)
    python -m finn --einmal              Postfach einmal abrufen und beenden
    python -m finn --datei plan.xlsx     Ohne Mail: Ergebnis nach out/ schreiben
    python -m finn --eml auftrag.eml     Auftrags-Mail aus Datei verarbeiten (Antwort nach out/)
    --referenz                           Freigegebene Vertragsauswertung statt LLM nutzen
"""
from __future__ import annotations

import argparse
import logging
import re
import tempfile
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

from .bericht import erstelle_bericht
from .extraktion import lade_vertraege
from .wirtschaftsplan import WirtschaftsplanFehler, lade_plan

BASIS = Path(__file__).parent.parent
VERTRAEGE = BASIS / "data" / "vertraege"
REFERENZ = BASIS / "data" / "covenants_referenz.json"
log = logging.getLogger("finn")


def verarbeite(xlsx: Path, betreff: str = "", modus: str = "llm") -> dict:
    planjahr = None
    if m := re.search(r"\b(20\d{2})\b", betreff):
        planjahr = int(m.group(1))
    log.info("Wirtschaftsplan lesen: %s%s", xlsx.name, f" (Planjahr {planjahr} laut Betreff)" if planjahr else "")
    plan = lade_plan(xlsx, planjahr)
    log.info("  Planjahr %s, %d Darlehen, Abgleiche: %s", plan.planjahr, len(plan.darlehen),
             "ok" if not plan.warnungen else plan.warnungen)
    log.info("Kreditverträge lesen (%s) …", modus)
    auswertungen, quelle = lade_vertraege(VERTRAEGE, REFERENZ, modus)
    log.info("  Quelle: %s", quelle)
    for a in auswertungen:
        log.info("  %-45s %d Covenant(s), Zitate %d/%d%s", a.datei[:45], len(a.covenants), a.zitate_ok,
                 a.zitate_geprueft, " (Cache)" if a.aus_cache else "")
    log.info("Kennzahlen rechnen und Antwort formulieren …")
    bericht = erstelle_bericht(plan, auswertungen, quelle)
    bericht["planjahr"] = plan.planjahr
    return bericht


def _anhaenge(bericht: dict) -> list[tuple[str, bytes, str]]:
    return [(f"Pruefpfad_Covenants_{bericht['planjahr']}.json", bericht["pruefpfad"].encode(), "application/json")]


def bearbeite_auftrag(auftrag, modus: str, senden: bool = True):
    from . import postfach
    log.info("Auftrag von %s: „%s“", auftrag.absender, auftrag.betreff)
    if not auftrag.anhang:
        text = ("Hallo,\n\ndanke für den Auftrag. Mir fehlt der Wirtschaftsplan als Excel-Anhang (.xlsx). "
                "Bitte schick ihn mir als Antwort auf diese Mail, dann prüfe ich die Covenants aller Kreditverträge.\n\n"
                "Viele Grüße\nFinn")
        antwort = postfach.baue_antwort(auftrag, text)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            pfad = Path(tmp) / Path(auftrag.anhang_name).name
            pfad.write_bytes(auftrag.anhang)
            try:
                b = verarbeite(pfad, auftrag.betreff, modus)
                antwort = postfach.baue_antwort(auftrag, b["text"], b["html"], _anhaenge(b))
            except WirtschaftsplanFehler as e:
                text = (f"Hallo,\n\nich konnte den Wirtschaftsplan „{auftrag.anhang_name}“ nicht vollständig lesen:\n"
                        f"{e}\n\nBitte prüf die Datei (Blätter GuV, Bilanz, Finanzierung mit Planspalte) und schick "
                        "sie mir erneut. Ich rechne lieber nicht, als mit einer unsicheren Zahl.\n\nViele Grüße\nFinn")
                antwort = postfach.baue_antwort(auftrag, text)
    if senden:
        postfach.sende(antwort)
        log.info("Antwort gesendet an %s", auftrag.absender)
    return antwort


def postfach_schleife(modus: str, einmal: bool, intervall: int) -> None:
    from . import postfach
    log.info("Finn überwacht %s (alle %ss) …", __import__("os").getenv("FINN_EMAIL"), intervall)
    while True:
        try:
            for auftrag in postfach.hole_auftraege():
                try:
                    bearbeite_auftrag(auftrag, modus)
                except Exception:
                    log.error("Fehler bei Auftrag %s:\n%s", auftrag.message_id, traceback.format_exc())
                    try:  # Absender nicht im Unklaren lassen
                        postfach.sende(postfach.baue_antwort(auftrag,
                            "Hallo,\n\nbei der Bearbeitung ist ein technischer Fehler aufgetreten. "
                            "Ich habe ihn protokolliert; bitte versuch es in ein paar Minuten erneut.\n\nViele Grüße\nFinn"))
                    except Exception:
                        log.error("Auch die Fehlermeldung konnte nicht gesendet werden.")
                postfach.merke_verarbeitet(auftrag.message_id)
                postfach.markiere_gelesen(auftrag.uid)
        except Exception as e:  # Netzwerk weg o. Ä.: weiterlaufen
            log.warning("Postfach nicht erreichbar (%s), neuer Versuch folgt.", e)
        if einmal:
            return
        time.sleep(intervall)


def main() -> None:
    import os
    load_dotenv(BASIS / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Finn – Covenant-Prüfung per E-Mail")
    ap.add_argument("--einmal", action="store_true")
    ap.add_argument("--datei", type=Path)
    ap.add_argument("--eml", type=Path)
    ap.add_argument("--referenz", action="store_true", help="freigegebene Vertragsauswertung statt LLM")
    args = ap.parse_args()
    modus = "referenz" if args.referenz else "llm"
    out = BASIS / "out"

    if args.datei:
        b = verarbeite(args.datei, "", modus)
        out.mkdir(exist_ok=True)
        (out / "antwort.html").write_text(f"<html><meta charset='utf-8'><body>{b['html']}</body></html>", encoding="utf-8")
        (out / "pruefpfad.json").write_text(b["pruefpfad"], encoding="utf-8")
        print("\n" + b["text"] + f"\n\n→ {out/'antwort.html'}")
    elif args.eml:
        from .postfach import lies_auftrag
        auftrag, _ = lies_auftrag(args.eml.read_bytes())
        antwort = bearbeite_auftrag(auftrag, modus, senden=False)
        out.mkdir(exist_ok=True)
        (out / "antwort.eml").write_bytes(bytes(antwort))
        print(f"→ {out/'antwort.eml'}")
    else:
        postfach_schleife(modus, args.einmal, int(os.getenv("FINN_POLL_SEKUNDEN", "20")))


if __name__ == "__main__":
    main()
