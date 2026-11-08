"""Liest den Wirtschaftsplan (xlsx) robust ein.

Grundsätze:
- Positionen werden über ihren Zeilentext gefunden, nicht über feste Zellen.
  Fügt jemand eine Zeile ein, findet der Parser die Position trotzdem.
- Die Planspalte wird über die Kopfzeile gefunden (Planjahr, z. B. "2027").
- Summen rechnet der Code selbst aus den Einzelpositionen nach und gleicht sie
  mit den im Excel gespeicherten Summen ab. Abweichungen landen als Warnung im Ergebnis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from .werte import Wert, summe


class WirtschaftsplanFehler(Exception):
    pass


@dataclass
class Darlehen:
    name: str
    stand_ende: Wert
    auszahlung: Wert
    planm_tilgung: Wert
    sondertilgung: Wert
    zinsaufwand: Wert
    gesellschafterdarlehen: bool
    nachrangig: bool


@dataclass
class Plan:
    planjahr: int
    # Erfolgsplan
    jahresueberschuss: Wert
    steuern_ertrag: Wert          # positiv = Aufwand
    zinsaufwand_guv: Wert         # positiv = Aufwand, gesamte GuV-Zeile
    aufzinsung_rueckstellungen: Wert  # positiv = Aufwand, "davon"-Zeile
    zinsertraege: Wert
    abschreibungen: Wert          # positiv = Aufwand
    # Planbilanz
    eigenkapital: Wert
    sonderposten: Wert
    bilanzsumme: Wert
    liquide_mittel: Wert
    verb_kreditinstitute: Wert
    gesellschafterdarlehen_bilanz: Wert
    # Darlehensspiegel
    darlehen: list[Darlehen]
    warnungen: list[str] = field(default_factory=list)
    stand: str = ""               # z. B. "Stand: 15.10.2026 · vom Aufsichtsrat noch nicht beschlossen (Entwurf)"
    datei: str = ""


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


class _Blatt:
    """Hilfsklasse: ein Tabellenblatt mit Formel- und Wertsicht."""

    def __init__(self, ws_f: Worksheet, ws_v: Worksheet, dateiname: str):
        self.f, self.v, self.titel, self.datei = ws_f, ws_v, ws_f.title, dateiname

    def zeilen(self):
        for r in range(1, self.f.max_row + 1):
            yield r, _norm(self.f.cell(r, 1).value)

    def finde_zeile(self, *muster: str, ausser: tuple[str, ...] = (), ab: int = 1) -> int:
        """Erste Zeile, deren Text alle Muster enthält (und keins der Ausschlussmuster)."""
        for r, text in self.zeilen():
            if r < ab:
                continue
            if all(m in text for m in muster) and not any(a in text for a in ausser):
                return r
        raise WirtschaftsplanFehler(
            f"Blatt '{self.titel}': keine Zeile mit {muster} gefunden")

    def finde_spalte(self, *muster: str, kopfzeilen: int = 6) -> int:
        for r in range(1, kopfzeilen + 1):
            for c in range(2, self.f.max_column + 1):
                text = _norm(self.f.cell(r, c).value)
                if text and all(m in text for m in muster):
                    return c
        raise WirtschaftsplanFehler(f"Blatt '{self.titel}': keine Spalte mit {muster}")

    def ist_formel(self, r: int, c: int) -> bool:
        v = self.f.cell(r, c).value
        return isinstance(v, str) and v.startswith("=")

    def zahl(self, r: int, c: int) -> float | None:
        v = self.f.cell(r, c).value
        if isinstance(v, (int, float)):
            return float(v)
        cached = self.v.cell(r, c).value  # nur für Formeln, Ergebnis aus Excel
        return float(cached) if isinstance(cached, (int, float)) else None

    def wert(self, r: int, c: int, vorzeichen: float = 1.0) -> Wert:
        z = self.zahl(r, c)
        if z is None:
            raise WirtschaftsplanFehler(f"{self.titel}!{self._adr(r, c)} ist leer")
        return Wert(vorzeichen * z, (self.quelle(r, c),))

    def quelle(self, r: int, c: int) -> str:
        label = str(self.f.cell(r, 1).value or "").strip()
        label = re.sub(r"\s*\(.*", "", label)  # Klammerzusätze kürzen
        return f"WP {self.titel}!{self._adr(r, c)} „{label}“"

    @staticmethod
    def _adr(r: int, c: int) -> str:
        return f"{openpyxl.utils.get_column_letter(c)}{r}"


def _summe_einzelposten(b: _Blatt, von: int, bis: int, c: int) -> Wert:
    """Summiert alle Nicht-Formel-Zahlen zwischen zwei Zeilen (exklusive Grenzen)."""
    werte = [b.wert(r, c) for r in range(von + 1, bis)
             if not b.ist_formel(r, c) and b.zahl(r, c) is not None]
    return summe(werte)


def _abgleich(b: _Blatt, r: int, c: int, gerechnet: Wert, was: str, warnungen: list[str]):
    gespeichert = b.zahl(r, c) if b.ist_formel(r, c) else None
    if gespeichert is not None and abs(gespeichert - gerechnet.betrag) > 0.5:
        warnungen.append(
            f"{was}: Excel zeigt {gespeichert:,.0f}, Nachrechnung ergibt "
            f"{gerechnet.betrag:,.0f} ({b.quelle(r, c)}). Verwendet: Nachrechnung.")


def lade_plan(pfad: str | Path, planjahr: int | None = None) -> Plan:
    pfad = Path(pfad)
    wb_f = openpyxl.load_workbook(pfad, data_only=False)
    wb_v = openpyxl.load_workbook(pfad, data_only=True)

    def blatt(*namen: str) -> _Blatt:
        for ws in wb_f.worksheets:
            if any(n in _norm(ws.title) for n in namen):
                return _Blatt(ws, wb_v[ws.title], pfad.name)
        raise WirtschaftsplanFehler(f"Kein Blatt gefunden für {namen}")

    guv, bil, fin = blatt("guv", "erfolg"), blatt("bilanz", "vermögen"), blatt("finanz", "darlehen")
    warn: list[str] = []

    if planjahr is None:
        kopf = " ".join(_norm(guv.f.cell(r, c).value)
                        for r in range(1, 6) for c in range(2, guv.f.max_column + 1))
        jahre = re.findall(r"plan (\d{4})", kopf)
        if not jahre:
            raise WirtschaftsplanFehler("Planjahr nicht in der GuV-Kopfzeile gefunden")
        planjahr = int(jahre[-1])
    j = str(planjahr)

    # ---------- Erfolgsplan ----------
    cg = guv.finde_spalte("plan", j)
    erste = guv.finde_zeile("umsatzerl")
    r_steuern = guv.finde_zeile("steuern vom einkommen")
    r_zinsaufw = guv.finde_zeile("zinsen und ähnliche aufwendungen")
    r_aufzins = guv.finde_zeile("davon", "aufzinsung")
    r_jue = guv.finde_zeile("jahresüberschuss")

    # Jahresüberschuss aus allen Einzelposten nachrechnen ("davon"-Zeilen nicht doppelt zählen)
    posten = [guv.wert(r, cg) for r in range(erste, r_jue)
              if not guv.ist_formel(r, cg) and guv.zahl(r, cg) is not None
              and not _norm(guv.f.cell(r, 1).value).startswith("davon")]
    jue = summe(posten)
    _abgleich(guv, r_jue, cg, jue, "Jahresüberschuss", warn)
    jue = Wert(jue.betrag, (guv.quelle(r_jue, cg),))

    # ---------- Planbilanz ----------
    cb = bil.finde_spalte(j)
    r_akt, r_bsum = bil.finde_zeile("aktiva", ausser=("summe",)), bil.finde_zeile("summe aktiva")
    bilanzsumme = _summe_einzelposten(bil, r_akt, r_bsum, cb)
    _abgleich(bil, r_bsum, cb, bilanzsumme, "Bilanzsumme", warn)

    r_pas, r_ek = bil.finde_zeile("passiva"), bil.finde_zeile("eigenkapital", ausser=("rücklage",))
    eigenkapital = _summe_einzelposten(bil, r_pas, r_ek, cb)
    _abgleich(bil, r_ek, cb, eigenkapital, "Eigenkapital", warn)

    r_psum = bil.finde_zeile("summe passiva")
    passiva = _summe_einzelposten(bil, r_pas, r_psum, cb)
    if abs(passiva.betrag - bilanzsumme.betrag) > 0.5:
        warn.append(f"Planbilanz nicht ausgeglichen: Aktiva {bilanzsumme.betrag:,.0f} "
                    f"≠ Passiva {passiva.betrag:,.0f}")

    plan = Plan(
        planjahr=planjahr,
        jahresueberschuss=jue,
        steuern_ertrag=guv.wert(r_steuern, cg, -1),
        zinsaufwand_guv=guv.wert(r_zinsaufw, cg, -1),
        aufzinsung_rueckstellungen=guv.wert(r_aufzins, cg, -1),
        zinsertraege=guv.wert(guv.finde_zeile("sonstige zinsen und ähnliche erträge"), cg),
        abschreibungen=guv.wert(guv.finde_zeile("abschreibungen"), cg, -1),
        # Summen mit der Excel-Zelle der Summenzeile als Fundstelle (nachgerechnet, s. o.)
        eigenkapital=Wert(eigenkapital.betrag, (bil.quelle(r_ek, cb),)),
        sonderposten=bil.wert(bil.finde_zeile("sonderposten"), cb),
        bilanzsumme=Wert(bilanzsumme.betrag, (bil.quelle(r_bsum, cb),)),
        liquide_mittel=bil.wert(bil.finde_zeile("kassenbestand"), cb),
        verb_kreditinstitute=bil.wert(bil.finde_zeile("verbindlichkeiten gegenüber kreditinstitut"), cb),
        gesellschafterdarlehen_bilanz=bil.wert(bil.finde_zeile("verbindlichkeiten gegenüber gesellschafter"), cb),
        darlehen=[],
        warnungen=warn,
    )

    # ---------- Darlehensspiegel ----------
    sp = {name: fin.finde_spalte(*m) for name, m in {
        "ende": ("stand", "31.12"), "ausz": ("auszahlung",), "tilg": ("planmäßige tilgung",),
        "sonder": ("sondertilgung",), "zins": ("zinsaufwand",)}.items()}
    for r, text in fin.zeilen():
        if not text or text.startswith(("summe", "abgleich", "darlehen")) or fin.ist_formel(r, sp["tilg"]):
            continue
        if fin.zahl(r, sp["tilg"]) is None:
            continue  # Zwischenüberschrift
        ende = _darlehen_stand_ende(fin, r, sp)
        plan.darlehen.append(Darlehen(
            name=str(fin.f.cell(r, 1).value).strip(),
            stand_ende=ende,
            auszahlung=fin.wert(r, sp["ausz"]),
            planm_tilgung=fin.wert(r, sp["tilg"]),
            sondertilgung=fin.wert(r, sp["sonder"]),
            zinsaufwand=fin.wert(r, sp["zins"]),
            gesellschafterdarlehen="gesellschafter" in text,
            nachrangig="nachrangig" in text or "rangrücktritt" in text,
        ))
    if not plan.darlehen:
        raise WirtschaftsplanFehler("Darlehensspiegel enthält keine Darlehen")

    _abgleiche_darlehensspiegel(plan)
    plan.datei = pfad.name
    for ws in wb_f.worksheets:  # Planstand vom Deckblatt, falls vorhanden
        for row in ws.iter_rows(max_row=15, max_col=3, values_only=True):
            for v in row:
                if isinstance(v, str) and v.strip().lower().startswith("stand"):
                    plan.stand = v.strip()
                    break
            if plan.stand:
                break
        if plan.stand:
            break
    return plan


def _darlehen_stand_ende(fin: _Blatt, r: int, sp: dict[str, int]) -> Wert:
    """Endbestand = Anfang + Auszahlung − Tilgung − Sondertilgung, selbst gerechnet."""
    c_anf = fin.finde_spalte("stand", "01.01")
    anf = fin.zahl(r, c_anf) or 0.0
    gerechnet = anf + sum((fin.zahl(r, sp[k]) or 0.0) * s
                          for k, s in (("ausz", 1), ("tilg", -1), ("sonder", -1)))
    return Wert(gerechnet, (fin.quelle(r, sp["ende"]),))


def _abgleiche_darlehensspiegel(plan: Plan) -> None:
    w = plan.warnungen
    banken = sum(d.stand_ende.betrag for d in plan.darlehen if not d.gesellschafterdarlehen)
    if abs(banken - plan.verb_kreditinstitute.betrag) > 0.5:
        w.append(f"Darlehensspiegel (Kreditinstitute {banken:,.0f}) ≠ Planbilanz "
                 f"({plan.verb_kreditinstitute.betrag:,.0f})")
    gesell = sum(d.stand_ende.betrag for d in plan.darlehen if d.gesellschafterdarlehen)
    if abs(gesell - plan.gesellschafterdarlehen_bilanz.betrag) > 0.5:
        w.append(f"Gesellschafterdarlehen im Darlehensspiegel ({gesell:,.0f}) ≠ Planbilanz "
                 f"({plan.gesellschafterdarlehen_bilanz.betrag:,.0f})")
    zins = sum(d.zinsaufwand.betrag for d in plan.darlehen)
    guv_ohne_aufzins = plan.zinsaufwand_guv.betrag - plan.aufzinsung_rueckstellungen.betrag
    if abs(zins - guv_ohne_aufzins) > 0.5:
        w.append(f"Zinsaufwand Darlehensspiegel ({zins:,.0f}) ≠ GuV ohne Aufzinsung "
                 f"({guv_ohne_aufzins:,.0f})")
