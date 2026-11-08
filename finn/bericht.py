"""Baut die Ergebnis-Mail. Tabellen und Zahlen: Code. Fließtext-Zusammenfassung: optional LLM,
abgesichert durch einen Zahlen-Wächter (jede Zahl im LLM-Text muss aus dem Code stammen)."""
from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .extraktion import Vertragsauswertung
from .rechnen import Covenant, Ergebnis, pruefe
from .wirtschaftsplan import Plan

KNAPP_SCHWELLE = 0.10  # Headroom < 10 % des Grenzwerts → "knapp"


# ---------- Formatierung ----------

def z(x: float, stellen: int = 0) -> str:
    s = f"{x:,.{stellen}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def kennzahl_text(cov: Covenant, x: float) -> str:
    if cov.typ == "eigenkapitalquote":
        return f"{z(x, cov.rundung_stellen if cov.rundung_stellen is not None else 2)} %"
    return z(x, cov.rundung_stellen if cov.rundung_stellen is not None else 2)


def headroom_text(e: Ergebnis) -> str:
    st = e.covenant.rundung_stellen if e.covenant.rundung_stellen is not None else 2
    vz = "+" if e.headroom >= 0 else "−"
    einheit = " Pp." if e.covenant.typ == "eigenkapitalquote" else ""
    return f"{vz}{z(abs(e.headroom), st)}{einheit}"


def grenz_text(cov: Covenant) -> str:
    zeichen = "≥" if cov.operator == ">=" else "≤"
    if cov.typ == "eigenkapitalquote":
        return f"{zeichen} {z(cov.grenzwert, 1)} %"
    return f"{zeichen} {z(cov.grenzwert, 2)}"


def status(e: Ergebnis) -> tuple[str, str]:
    if not e.eingehalten:
        return "nein – Unterschreitung", "#c62828"
    if abs(e.headroom) < KNAPP_SCHWELLE * e.covenant.grenzwert:
        return "ja – knapp", "#ef6c00"
    return "ja", "#2e7d32"


def quellen_kurz(quellen: tuple[str, ...]) -> str:
    if len(quellen) <= 2:
        return "; ".join(quellen)
    zellen: dict[str, list[str]] = {}
    for q in quellen:
        m = re.match(r"WP (\w+)!(\w+)", q)
        if m:
            zellen.setdefault(m.group(1), []).append(m.group(2))
    return "; ".join(f"WP {b}!{', '.join(c)}" for b, c in zellen.items())


# ---------- Analyse ----------

@dataclass
class Abgrenzung:
    text: str
    alternativ_wert: str
    alternativ_eingehalten: bool


PARAM_TEXT = {
    "nachrangige_gesellschafterdarlehen_einbeziehen": ("nachrangiges Gesellschafterdarlehen", "einbezogen", "nicht einbezogen"),
    "gesellschafterdarlehen_einbeziehen": ("Gesellschafterdarlehen (Zins und Tilgung)", "einbezogen", "nicht einbezogen"),
    "sondertilgung_einbeziehen": ("Sondertilgung", "einbezogen", "nicht einbezogen"),
    "ebitda_aufzinsung_einbeziehen": ("Aufzinsung Pensionsrückstellungen im EBITDA", "hinzugerechnet", "nicht hinzugerechnet"),
}


def abgrenzungen(plan: Plan, e: Ergebnis) -> list[Abgrenzung]:
    """Kontrafaktisch: Was käme heraus, wenn man die Vertragsdefinition an dieser Stelle anders liest?
    Zeigt nur Abgrenzungen, die das Ergebnis tatsächlich verändern."""
    cov, aus = e.covenant, []
    kandidaten = dict(cov.parameter)
    if cov.typ == "eigenkapitalquote":
        kandidaten.setdefault("sonderposten_anteil", 0.0)
    for k, v in kandidaten.items():
        if isinstance(v, bool):
            alt = not v
        elif k == "sonderposten_anteil":
            alt = 0.0 if v else 0.5
        else:
            continue
        alt_e = pruefe(plan, replace(cov, parameter={**cov.parameter, k: alt}))
        if abs(alt_e.wert - e.wert) < 1e-9:
            continue
        if k == "sonderposten_anteil":
            text = (f"Sonderposten zu {v:.0%} angerechnet (Vertrag); bei {alt:.0%}"
                    .replace(".", ","))
        else:
            name, ja, nein = PARAM_TEXT.get(k, (k, "ja", "nein"))
            text = f"{name} laut Vertrag {ja if v else nein}; {ja if alt else nein} ergäbe sich"
        aus.append(Abgrenzung(text, kennzahl_text(cov, alt_e.wert), alt_e.eingehalten))
    return aus


def marge(a: Vertragsauswertung, ergebnisse: list[Ergebnis]) -> str | None:
    mr = a.roh.get("margenraster")
    if not mr:
        return None
    e = next((x for x in ergebnisse if x.covenant.typ == mr["kennzahl_typ"]), None)
    if e is None:
        return None
    stufen = sorted(mr["stufen"], key=lambda s: float("inf") if s["bis_einschliesslich"] is None else s["bis_einschliesslich"])
    for s in stufen:
        if s["bis_einschliesslich"] is None or e.wert <= s["bis_einschliesslich"]:
            beste = stufen[0]["marge_prozent"]
            return (f"Margenraster ({mr['paragraph']}): {e.covenant.kennzahl} {kennzahl_text(e.covenant, e.wert)} "
                    f"→ Marge {z(s['marge_prozent'], 2)} % p. a. (beste Stufe: {z(beste, 2)} %). "
                    "Kein Kündigungsgrund, aber höhere Zinskosten.")
    return None


def frist_hinweis(plan: Plan, frist: str) -> str:
    """Rechnet relative Fristen ('30 Tage nach Aufstellung') auf das Datum des Planstands um."""
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", plan.stand or "")
    t = re.search(r"(\d+) Tagen? nach (seiner )?Aufstellung", frist)
    if m and t:
        d = datetime.strptime(m.group(1), "%d.%m.%Y") + timedelta(days=int(t.group(1)))
        return f" → bei Planstand {m.group(1)}: bis {d:%d.%m.%Y}"
    return ""


# ---------- Zusammenfassung ----------

def fakten(plan: Plan, paare: list[tuple[Vertragsauswertung, Ergebnis]], ohne: list[Vertragsauswertung]) -> dict:
    return {
        "planjahr": plan.planjahr,
        "stichtag": f"31.12.{plan.planjahr}",
        "covenants": [{"vertrag": a.vertrag, "kennzahl": e.covenant.kennzahl,
                       "wert": kennzahl_text(e.covenant, e.wert), "grenzwert": grenz_text(e.covenant),
                       "headroom": headroom_text(e), "status": status(e)[0], "puffer": e.puffer}
                      for a, e in paare],
        "ohne_covenants": [a.vertrag for a in ohne],
    }


def zusammenfassung_vorlage(f: dict) -> str:
    n = len(f["covenants"])
    brueche = [c for c in f["covenants"] if c["status"].startswith("nein")]
    knapp = [c for c in f["covenants"] if c["status"] == "ja – knapp"]
    s = (f"Auf Basis des Wirtschaftsplans {f['planjahr']} (Stichtag {f['stichtag']}) werden "
         + (f"alle {n} Financial Covenants eingehalten." if not brueche
            else f"{n - len(brueche)} von {n} Financial Covenants eingehalten."))
    if brueche:
        s += " Nicht eingehalten: " + "; ".join(f"{c['kennzahl']} ({c['vertrag']}) mit {c['wert']}" for c in brueche) + "."
    if knapp:
        s += " Knapp sind: " + "; ".join(f"{c['kennzahl']} mit {c['wert']} bei Grenze {c['grenzwert']}" for c in knapp) + "."
    if f["ohne_covenants"]:
        s += " Ohne Financial Covenants: " + "; ".join(f["ohne_covenants"]) + "."
    return s


def _zahlen(text: str) -> set[str]:
    return {m.rstrip(".,") for m in re.findall(r"\d[\d.,]*", text)}


def zusammenfassung_llm(f: dict) -> tuple[str, str]:
    """LLM formuliert; Zahlen-Wächter lässt nur Zahlen durch, die im Fakten-JSON stehen."""
    vorlage = zusammenfassung_vorlage(f)
    if os.getenv("FINN_LLM_ZUSAMMENFASSUNG", "1") != "1" or not os.getenv("ANTHROPIC_API_KEY"):
        return vorlage, "Vorlage"
    try:
        import anthropic
        r = anthropic.Anthropic().messages.create(
            model=os.getenv("FINN_MODELL_TEXT", os.getenv("FINN_MODELL", "claude-opus-5-5")),
            max_tokens=4000,
            system=("Du bist Finn, Referent Finanzierung. Schreibe für die Leitung Finanzen eine sachliche "
                    "Zusammenfassung in 3–4 Sätzen auf Deutsch, ohne Anrede und ohne Grußformel. Verwende "
                    "ausschließlich Zahlen, die wörtlich in den Fakten stehen. Rechne nichts. Hebe knappe "
                    "Covenants und den kritischsten Punkt hervor."),
            messages=[{"role": "user", "content": json.dumps(f, ensure_ascii=False)}],
        )
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        text = text.replace("**", "").replace("__", "")   # kein Markdown in der Mail
        if len(text) < 40:
            return vorlage, f"Vorlage (LLM-Text leer, stop_reason={r.stop_reason})"
        erlaubt = _zahlen(json.dumps(f, ensure_ascii=False))
        fremd = _zahlen(text) - erlaubt
        if fremd:
            return vorlage, f"Vorlage (LLM-Text verworfen, unbelegte Zahlen: {', '.join(sorted(fremd))})"
        return text, "LLM, Zahlen geprüft"
    except Exception as e:
        return vorlage, f"Vorlage (LLM nicht verfügbar: {type(e).__name__})"


# ---------- Mail ----------

TD = 'style="border:1px solid #ccc;padding:5px 8px;vertical-align:top"'
TH = 'style="border:1px solid #ccc;padding:5px 8px;background:#f0f3f7;text-align:left"'


def _t(s: object) -> str:
    return html.escape(str(s))


def erstelle_bericht(plan: Plan, auswertungen: list[Vertragsauswertung], quelle_vertraege: str) -> dict:
    paare: list[tuple[Vertragsauswertung, Ergebnis]] = []
    for a in auswertungen:
        for c in a.covenants:
            paare.append((a, pruefe(plan, c)))
    ohne = [a for a in auswertungen if not a.covenants and not a.nicht_berechenbar]
    f = fakten(plan, paare, ohne)
    text_zus, zus_quelle = zusammenfassung_llm(f)
    j = plan.planjahr

    h = ['<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;max-width:980px">',
         "<p>Hallo,</p>",
         f"<p>anbei die Covenant-Prüfung für den Wirtschaftsplan {j} (Stichtag 31.12.{j}).</p>",
         f'<p style="background:#f6f8fa;border-left:4px solid #1f4e79;padding:10px 12px">{_t(text_zus)}</p>']

    # 1. Übersicht
    h.append(f"<h3>1. Übersicht je Vertrag und Covenant</h3><table style='border-collapse:collapse'>"
             f"<tr><th {TH}>Vertrag</th><th {TH}>Covenant</th><th {TH}>Plan {j}</th><th {TH}>Grenzwert</th>"
             f"<th {TH}>Headroom</th><th {TH}>Eingehalten</th><th {TH}>Fundstelle Vertrag</th></tr>")
    for a, e in paare:
        st, farbe = status(e)
        h.append(f"<tr><td {TD}>{_t(a.vertrag)}</td><td {TD}>{_t(e.covenant.kennzahl)}</td>"
                 f"<td {TD}><b>{kennzahl_text(e.covenant, e.wert)}</b></td><td {TD}>{grenz_text(e.covenant)}</td>"
                 f"<td {TD}>{headroom_text(e)}</td><td {TD}><b style='color:{farbe}'>{st}</b></td>"
                 f"<td {TD}>{_t(e.covenant.fundstelle_grenzwert)}</td></tr>")
    for a in ohne:
        h.append(f"<tr><td {TD}>{_t(a.vertrag)}</td><td {TD} colspan='6'><i>Keine Financial Covenants.</i> "
                 f"{_t(a.roh.get('begruendung', ''))}</td></tr>")
    for a in auswertungen:
        for c in a.nicht_berechenbar:
            h.append(f"<tr><td {TD}>{_t(a.vertrag)}</td><td {TD}>{_t(c.get('kennzahl'))}</td>"
                     f"<td {TD} colspan='5'><b style='color:#ef6c00'>Nicht automatisch berechenbar – manuelle Prüfung</b></td></tr>")
    alt = [d for d in plan.darlehen if "altdarlehen" in d.name.lower()]
    if alt:
        h.append(f"<tr><td {TD}>{_t(alt[0].name)}</td><td {TD} colspan='6'><i>Verträge lagen nicht vor; "
                 "laut Erläuterung im Wirtschaftsplan ohne Financial Covenants.</i></td></tr>")
    h.append("</table>")

    # 2. Rechenweg und Belege
    h.append("<h3>2. Rechenweg und Fundstellen je Kennzahl</h3>")
    for a, e in paare:
        c = e.covenant
        h.append(f"<p style='margin-bottom:4px'><b>{_t(a.vertrag)} – {_t(c.kennzahl)}</b><br>"
                 f"Definition: {_t(c.definition_kurz)}<br>"
                 f"Grenzwert: {_t(c.fundstelle_grenzwert)} · Definition: {_t(c.fundstelle_definition)}"
                 + (f" · Rundung laut Vertrag: {c.rundung_stellen} Nachkommastelle(n)" if c.rundung_stellen is not None else "")
                 + f"<br>Rechnung (T€): {_t(e.rechenweg)}<br>Puffer: {_t(e.puffer)}</p>")
        h.append(f"<table style='border-collapse:collapse;margin-bottom:6px'><tr><th {TH}>Position</th>"
                 f"<th {TH}>T€</th><th {TH}>Fundstelle Wirtschaftsplan</th></tr>")
        for w in e.komponenten:
            h.append(f"<tr><td {TD}>{_t(w.bezeichnung)}</td><td {TD} align='right'>{z(w.betrag)}</td>"
                     f"<td {TD}>{_t(quellen_kurz(w.quellen))}</td></tr>")
        h.append("</table>")
        abg = abgrenzungen(plan, e)
        if abg:
            h.append("<p style='margin-top:0'><i>Abgrenzungen, die das Ergebnis ändern:</i></p><ul style='margin-top:0'>")
            for x in abg:
                folge = "eingehalten" if x.alternativ_eingehalten else "<b style='color:#c62828'>NICHT eingehalten</b>"
                h.append(f"<li>{_t(x.text)}: {x.alternativ_wert} ({folge})</li>")
            h.append("</ul>")

    # 3. Konsequenzen und Fristen
    h.append("<h3>3. Was daraus folgt</h3><ul>")
    for a in auswertungen:
        m = marge(a, [e for b, e in paare if b is a])
        if m:
            h.append(f"<li><b>{_t(a.vertrag)}:</b> {_t(m)}</li>")
    for a in auswertungen:
        for p in a.roh.get("pflichten_planjahr") or []:
            h.append(f"<li><b>{_t(a.vertrag)}</b> ({_t(p.get('paragraph', ''))}): {_t(p.get('pflicht', ''))} – "
                     f"Frist: {_t(p.get('frist', ''))}{_t(frist_hinweis(plan, p.get('frist', '')))}</li>")
    h.append("</ul>")

    # 4. Prüfhinweise
    zit = sum(a.zitate_ok for a in auswertungen), sum(a.zitate_geprueft for a in auswertungen)
    h.append("<h3>4. Prüfhinweise</h3><ul>")
    h.append(f"<li>Datengrundlage: {_t(plan.datei)} ({_t(plan.stand or 'ohne Standangabe')}), Werte in T€.</li>")
    h.append(f"<li>Vertragsauswertung: {_t(quelle_vertraege)}"
             + (f"; {zit[0]} von {zit[1]} Belegzitaten wörtlich im Vertrag gefunden, Seitenzahlen vom Code ermittelt."
                if zit[1] else ".") + "</li>")
    h.append("<li>Abgleiche im Wirtschaftsplan (Bilanz = Darlehensspiegel, Zinsaufwand = GuV ohne Aufzinsung, "
             "Aktiva = Passiva, Summen nachgerechnet): "
             + ("alle stimmig." if not plan.warnungen else "<b>Abweichungen:</b> " + _t("; ".join(plan.warnungen)))
             + "</li>")
    for a in auswertungen:
        for b in a.befunde:
            if b.ebene == "warnung":
                h.append(f"<li><b style='color:#ef6c00'>{_t(a.vertrag)}:</b> {_t(b.text)}</li>")
    h.append("<li>Planwerte: Kennzahlen auf Planbasis dienen der Frühwarnung; maßgeblich ist der festgestellte "
             "Jahresabschluss.</li>")
    h.append(f"<li>Alle Kennzahlen wurden im Code berechnet; das Sprachmodell hat nur gelesen und formuliert "
             f"(Zusammenfassung: {_t(zus_quelle)}).</li></ul>")
    h.append("<p>Viele Grüße<br>Finn<br><span style='color:#777'>Referent Finanzierung (digital)</span></p></div>")

    pruefpfad = {
        "planjahr": j, "wirtschaftsplan": plan.datei, "stand": plan.stand, "vertragsauswertung": quelle_vertraege,
        "warnungen_wirtschaftsplan": plan.warnungen,
        "ergebnisse": [{
            "vertrag": a.vertrag, "kennzahl": e.covenant.kennzahl, "typ": e.covenant.typ,
            "wert": e.wert, "wert_ungerundet": e.wert_ungerundet, "grenzwert": e.covenant.grenzwert,
            "operator": e.covenant.operator, "headroom": e.headroom, "eingehalten": e.eingehalten,
            "parameter": e.covenant.parameter, "fundstelle_grenzwert": e.covenant.fundstelle_grenzwert,
            "fundstelle_definition": e.covenant.fundstelle_definition, "rechenweg": e.rechenweg,
            "komponenten": [{"position": w.bezeichnung, "t_eur": w.betrag, "quellen": list(w.quellen)}
                            for w in e.komponenten]} for a, e in paare],
        "vertraege_roh": [{"datei": a.datei, **a.roh} for a in auswertungen],
    }
    alle_ok = all(e.eingehalten for _, e in paare)
    return {
        "html": "\n".join(h),
        "text": _als_text(text_zus, paare, ohne, j),
        "pruefpfad": json.dumps(pruefpfad, ensure_ascii=False, indent=2),
        "kurzstatus": "alle Covenants eingehalten" if alle_ok else "Covenant-Verletzung im Plan",
    }


def _als_text(zus: str, paare, ohne, j) -> str:
    zeilen = ["Hallo,", "", f"Covenant-Prüfung Wirtschaftsplan {j} (Stichtag 31.12.{j}).", "", zus, ""]
    for a, e in paare:
        zeilen.append(f"- {a.vertrag} | {e.covenant.kennzahl}: {kennzahl_text(e.covenant, e.wert)} "
                      f"(Grenze {grenz_text(e.covenant)}, Headroom {headroom_text(e)}) – {status(e)[0]} "
                      f"[{e.covenant.fundstelle_grenzwert}]")
    for a in ohne:
        zeilen.append(f"- {a.vertrag}: keine Financial Covenants")
    zeilen += ["", "Details, Rechenweg und Fundstellen in der HTML-Ansicht und im Prüfpfad (JSON-Anhang).",
               "", "Viele Grüße", "Finn"]
    return "\n".join(zeilen)
