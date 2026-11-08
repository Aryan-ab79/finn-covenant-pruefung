"""Vertragsextraktion: Das LLM liest und strukturiert, der Code prüft.

Ablauf je Vertrag:
1. PDF seitenweise lesen, Text mit Seitenmarken an das LLM geben.
2. Das LLM füllt ein festes Schema (Tool Use). Jede Aussage braucht ein wörtliches Zitat.
3. Der Code prüft jedes Zitat gegen den PDF-Text und bestimmt die Seite selbst.
   Der Grenzwert muss als Zahl im Grenzwert-Zitat stehen.
4. Ergebnis wird je PDF-Hash gecacht (Verträge ändern sich selten).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .pdf import als_prompt_text, finde_zitat, lese_seiten, zahl_im_text
from .rechnen import Covenant

PROMPT_VERSION = "v4"
log = logging.getLogger("finn")
CACHE_DIR = Path(os.getenv("FINN_CACHE_DIR", Path(__file__).parent.parent / "cache"))

_ZITAT = {
    "type": "object",
    "properties": {
        "seite": {"type": "integer"},
        "text": {"type": "string", "description": "Wörtlich aus dem Vertrag kopiert, 8–40 Wörter, ohne Auslassungen."},
    },
    "required": ["seite", "text"],
}

def _param(typ: str, beschreibung: str) -> dict:
    return {"type": "object", "description": beschreibung,
            "properties": {"wert": {"type": typ}, "beleg": _ZITAT}, "required": ["wert", "beleg"]}

SCHEMA = {
    "type": "object",
    "properties": {
        "vertrag": {"type": "string", "description": "KURZ, höchstens 8 Wörter: Bank – Darlehensart Nr. X, z. B. 'Landesbank Mittelland – Investitionsdarlehen Nr. 123'. Keine Beträge, Daten oder Konsortialmitglieder."},
        "hat_financial_covenants": {"type": "boolean"},
        "begruendung": {"type": "string", "description": "1–2 Sätze, warum (keine) Financial Covenants vorliegen."},
        "covenants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kennzahl": {"type": "string", "description": "Bezeichnung laut Vertrag"},
                    "typ": {"type": "string", "enum": ["eigenkapitalquote", "dyn_verschuldungsgrad", "dscr", "sonstige"]},
                    "operator": {"type": "string", "enum": [">=", "<="]},
                    "grenzwert": {"type": "number", "description": "Quoten in Prozentpunkten (27,5 % → 27.5), Faktoren als Zahl (1,30 → 1.3)"},
                    "rundung_stellen": {"type": ["integer", "null"], "description": "Nur wenn der Vertrag eine Rundung vorschreibt, sonst null"},
                    "paragraph_grenzwert": {"type": "string", "description": "z. B. '§ 14 Abs. 1 lit. b'"},
                    "paragraph_definition": {"type": "string", "description": "z. B. '§ 1 Abs. 1' oder 'Anlage 3 Abs. 2–6'"},
                    "zitat_grenzwert": _ZITAT,
                    "zitate_definition": {"type": "array", "items": _ZITAT, "minItems": 1},
                    "definition_kurz": {"type": "string", "description": "Formel in Worten, inkl. aller Ein- und Ausschlüsse"},
                    "parameter": {
                        "type": "object",
                        "description": "Nur die für den Typ relevanten Angaben füllen, jeweils mit Beleg.",
                        "properties": {
                            "sonderposten_anteil": _param("number", "eigenkapitalquote: Anteil der Sonderposten, der dem Eigenkapital zugerechnet wird (0 wenn keiner, 0.5 bei 50 %)"),
                            "nachrangige_gesellschafterdarlehen_einbeziehen": _param("boolean", "dyn_verschuldungsgrad: Zählen Gesellschafterdarlehen mit Rangrücktritt zu den Finanzverbindlichkeiten?"),
                            "gesellschafterdarlehen_einbeziehen": _param("boolean", "dscr: Zählen Zins und Tilgung von Gesellschafterdarlehen zum Schuldendienst?"),
                            "sondertilgung_einbeziehen": _param("boolean", "dscr: Zählen freiwillige Sondertilgungen zum Schuldendienst?"),
                            "ebitda_aufzinsung_einbeziehen": _param("boolean", "Wird beim EBITDA die gesamte GuV-Position 'Zinsen und ähnliche Aufwendungen' (inkl. Aufzinsung von Rückstellungen) hinzugerechnet? true, wenn die Definition keine Ausnahme nennt."),
                        },
                    },
                },
                "required": ["kennzahl", "typ", "operator", "grenzwert", "rundung_stellen", "paragraph_grenzwert",
                             "paragraph_definition", "zitat_grenzwert", "zitate_definition", "definition_kurz", "parameter"],
            },
        },
        "pflichten_planjahr": {
            "type": "array",
            "description": "Pflichten rund um Wirtschaftsplan/Planberechnung der Kennzahlen und Folgen einer Planunterschreitung",
            "items": {"type": "object", "properties": {
                "pflicht": {"type": "string"}, "frist": {"type": "string"},
                "paragraph": {"type": "string"}, "beleg": _ZITAT},
                "required": ["pflicht", "frist", "paragraph", "beleg"]},
        },
        "margenraster": {
            "type": ["object", "null"],
            "description": "Nur falls die Zinsmarge von einer Kennzahl abhängt",
            "properties": {
                "kennzahl_typ": {"type": "string", "enum": ["eigenkapitalquote", "dyn_verschuldungsgrad", "dscr"]},
                "paragraph": {"type": "string"},
                "stufen": {"type": "array", "items": {"type": "object", "properties": {
                    "bis_einschliesslich": {"type": ["number", "null"], "description": "Obergrenze der Stufe, null = offen"},
                    "marge_prozent": {"type": "number"}}, "required": ["bis_einschliesslich", "marge_prozent"]}},
                "beleg": _ZITAT},
        },
    },
    "required": ["vertrag", "hat_financial_covenants", "begruendung", "covenants", "pflichten_planjahr", "margenraster"],
}

SYSTEM = """Du bist Finn, Referent Finanzierung eines Stadtwerks. Du liest Kreditverträge so genau wie ein erfahrener Finanzreferent.

Aufgabe: Ermittle die Financial Covenants des Vertrags und fülle das Schema über das Werkzeug `vertragsauswertung`.

Regeln:
- Du rechnest NICHTS. Du liest, ordnest zu und belegst.
- Ein Financial Covenant ist eine Finanzkennzahl mit Grenzwert, die die Kreditnehmerin während der Laufzeit einhalten MUSS.
  Keine Covenants sind: Kennzahlen in der Präambel oder als Hintergrund der Kreditentscheidung, Beispielrechnungen,
  Margenraster (reine Preisbestimmung), Informationspflichten ohne Grenzwert.
- Lies jede Definition vollständig, auch in Begriffsbestimmungen (§ 1) und Anlagen. Achte besonders auf Ein- und Ausschlüsse:
  Sonderposten, Gesellschafterdarlehen mit/ohne Rangrücktritt, Rückstellungen, Sondertilgungen, Aufzinsung von Rückstellungen,
  Liquide Mittel. Verträge regeln dieselbe Position unterschiedlich – übertrage nichts von einem Vertrag auf einen anderen.
- Jedes Zitat ist wörtlich aus dem Vertragstext kopiert (keine Auslassungen, keine Umformulierung). Gib die Seite laut '=== Seite N ===' an.
  Das Grenzwert-Zitat muss den Grenzwert als Zahl enthalten.
- Passt eine Kennzahl auf keinen der Typen, nutze typ 'sonstige'.
"""


@dataclass
class Befund:
    ebene: str  # "ok" | "warnung"
    text: str


@dataclass
class Vertragsauswertung:
    datei: str
    roh: dict
    covenants: list[Covenant] = field(default_factory=list)
    nicht_berechenbar: list[dict] = field(default_factory=list)
    befunde: list[Befund] = field(default_factory=list)
    zitate_geprueft: int = 0
    zitate_ok: int = 0
    aus_cache: bool = False

    @property
    def vertrag(self) -> str:
        return self.roh.get("vertrag", self.datei)


def _llm_aufruf(seiten: tuple[str, ...], dateiname: str, modell: str) -> dict:
    import anthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY fehlt")
    client = anthropic.Anthropic()
    anfrage = dict(
        model=modell,
        max_tokens=32000,
        system=SYSTEM + "\nAntworte ausschließlich, indem du das Werkzeug `vertragsauswertung` genau einmal aufrufst.",
        tools=[{"name": "vertragsauswertung", "description": "Strukturierte Auswertung eines Kreditvertrags",
                "input_schema": SCHEMA}],
        messages=[{"role": "user", "content": f"Vertragsdatei: {dateiname}\n\n{als_prompt_text(seiten)}"}],
    )
    def senden(tool_choice: dict):
        # Streaming: bei großem Antwortlimit von der API verlangt, außerdem robuster gegen Timeouts.
        with client.messages.stream(**anfrage, tool_choice=tool_choice) as stream:
            return stream.get_final_message()

    # tool_choice "auto": Aktuelle Modelle denken vor der Antwort nach ("thinking") und erlauben
    # daher kein erzwungenes Werkzeug. Der Systemprompt verlangt den Werkzeugaufruf; fehlt er, Fehler.
    try:
        antwort = senden({"type": "auto"})
    except anthropic.BadRequestError as e:
        log.info("  %s: Anfrage abgelehnt (%s), versuche erzwungenes Werkzeug", dateiname, _fehlertext(e))
        antwort = senden({"type": "tool", "name": "vertragsauswertung"})
    for block in antwort.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError(f"Keine strukturierte Antwort für {dateiname} (stop_reason={antwort.stop_reason})")


def _fehlertext(e: Exception) -> str:
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        return str(body.get("error", {}).get("message", body))[:300]
    return str(e)[:300]


def extrahiere(pdf: Path, modell: str | None = None, cache: bool = True, llm=_llm_aufruf) -> Vertragsauswertung:
    modell = modell or os.getenv("FINN_MODELL", "claude-opus-5-5")
    seiten = lese_seiten(str(pdf))
    h = hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]
    cache_datei = CACHE_DIR / f"{pdf.stem}_{h}_{modell}_{PROMPT_VERSION}.json"

    if cache and cache_datei.exists():
        roh, aus_cache = json.loads(cache_datei.read_text(encoding="utf-8")), True
    else:
        roh, aus_cache = llm(seiten, pdf.name, modell), False
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_datei.write_text(json.dumps(roh, ensure_ascii=False, indent=2), encoding="utf-8")

    a = pruefe_auswertung(roh, seiten, pdf.name)
    a.aus_cache = aus_cache
    return a


def pruefe_auswertung(roh: dict, seiten: tuple[str, ...], dateiname: str) -> Vertragsauswertung:
    """Verifiziert alle Zitate und baut daraus rechenbare Covenants."""
    a = Vertragsauswertung(datei=dateiname, roh=roh)

    def beleg(z: dict | None, was: str) -> int | None:
        if not z:
            a.befunde.append(Befund("warnung", f"{was}: kein Beleg angegeben"))
            return None
        a.zitate_geprueft += 1
        f = finde_zitat(seiten, z.get("text", ""), z.get("seite"))
        if not f.gefunden:
            a.befunde.append(Befund("warnung", f"{was}: Zitat nicht im Vertrag gefunden – manuell prüfen"))
            return None
        a.zitate_ok += 1
        if f.seite != f.seite_laut_llm:
            a.befunde.append(Befund("ok", f"{was}: Seite korrigiert ({f.seite_laut_llm} → {f.seite})"))
        z["seite_geprueft"] = f.seite
        return f.seite

    if roh.get("hat_financial_covenants") and not roh.get("covenants"):
        a.befunde.append(Befund("warnung", "Covenants gemeldet, aber keine Details geliefert"))

    for c in roh.get("covenants", []):
        name = c.get("kennzahl", "?")
        s_grenz = beleg(c.get("zitat_grenzwert"), f"{name} – Grenzwert")
        if s_grenz and not zahl_im_text(c["grenzwert"], c["zitat_grenzwert"]["text"]):
            a.befunde.append(Befund("warnung", f"{name}: Grenzwert {c['grenzwert']} steht nicht im Zitat"))
        s_def = sorted({s for z in c.get("zitate_definition", []) if (s := beleg(z, f"{name} – Definition"))})

        params, belegt = {}, True
        for key, p in (c.get("parameter") or {}).items():
            if isinstance(p, dict) and "wert" in p:
                params[key] = p["wert"]
                belegt &= beleg(p.get("beleg"), f"{name} – {key}") is not None

        if c.get("typ") == "sonstige":
            a.nicht_berechenbar.append(c)
            a.befunde.append(Befund("warnung", f"{name}: Kennzahltyp nicht automatisch berechenbar"))
            continue

        a.covenants.append(Covenant(
            vertrag=roh.get("vertrag", dateiname),
            kennzahl=name,
            typ=c["typ"],
            operator=c["operator"],
            grenzwert=float(c["grenzwert"]),
            rundung_stellen=c.get("rundung_stellen"),
            fundstelle_grenzwert=_fundstelle(c.get("paragraph_grenzwert", ""), [s_grenz] if s_grenz else []),
            fundstelle_definition=_fundstelle(c.get("paragraph_definition", ""), s_def),
            definition_kurz=c.get("definition_kurz", ""),
            parameter=params,
        ))
        if not belegt:
            a.befunde.append(Befund("warnung", f"{name}: mindestens eine Abgrenzung ohne gültigen Beleg"))

    for p in roh.get("pflichten_planjahr", []):
        p["seite_geprueft"] = beleg(p.get("beleg"), f"Pflicht {p.get('paragraph', '')}")
    if roh.get("margenraster"):
        beleg(roh["margenraster"].get("beleg"), "Margenraster")
    return a


def _fundstelle(paragraph: str, seiten: list[int]) -> str:
    if not seiten:
        return f"{paragraph} (Seite nicht verifiziert)"
    s = f"S. {seiten[0]}" if len(seiten) == 1 else f"S. {seiten[0]}–{seiten[-1]}"
    return f"{paragraph}, {s}"


def extrahiere_alle(ordner: Path, **kw) -> list[Vertragsauswertung]:
    pdfs = sorted(ordner.glob("*.pdf"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda p: extrahiere(p, **kw), pdfs))


def lade_referenz(pfad: Path) -> list[Vertragsauswertung]:
    """Freigegebene (von Hand geprüfte) Vertragsauswertung – Rückfallebene, wenn das LLM nicht erreichbar ist."""
    daten = json.loads(Path(pfad).read_text(encoding="utf-8"))
    ergebnis = []
    for v in daten["vertraege"]:
        roh = {k: v.get(k) for k in ("vertrag", "hat_financial_covenants", "begruendung",
                                       "pflichten_planjahr", "margenraster")}
        roh["covenants"] = []
        a = Vertragsauswertung(datei=v["datei"], roh=roh)
        a.covenants = [Covenant(vertrag=v["vertrag"], **c) for c in v["covenants"]]
        a.befunde.append(Befund("ok", "Freigegebene Vertragsauswertung verwendet (Vier-Augen-geprüft)"))
        ergebnis.append(a)
    return ergebnis


def lade_vertraege(ordner: Path, referenz: Path | None, modus: str = "llm") -> tuple[list[Vertragsauswertung], str]:
    """modus 'llm': LLM-Extraktion (gecacht); fällt bei Fehler auf die freigegebene Referenz zurück.
    modus 'referenz': direkt die freigegebene Auswertung."""
    if modus == "referenz" and referenz and referenz.exists():
        return lade_referenz(referenz), "freigegebene Vertragsauswertung (Referenz)"
    try:
        return extrahiere_alle(ordner), "LLM-Extraktion mit Zitatprüfung"
    except Exception as e:  # API down, Key fehlt, Timeout …
        log.warning("LLM-Extraktion fehlgeschlagen: %s", _fehlertext(e))
        if referenz and referenz.exists():
            return lade_referenz(referenz), f"freigegebene Vertragsauswertung (LLM nicht verfügbar: {_fehlertext(e)[:120]})"
        raise
