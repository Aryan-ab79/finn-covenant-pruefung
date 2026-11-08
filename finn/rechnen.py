"""Rechenkern. Hier entsteht jede Zahl – das Sprachmodell rechnet nie.

Die Vertragsunterschiede (z. B. ob ein nachrangiges Gesellschafterdarlehen
mitzählt) kommen als Parameter aus der Vertragsextraktion. Die Formeln selbst
sind fest im Code und getestet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .werte import Wert, kaufm_runden, summe
from .wirtschaftsplan import Plan

KennzahlTyp = Literal["eigenkapitalquote", "dyn_verschuldungsgrad", "dscr"]


@dataclass
class Covenant:
    """Ein Financial Covenant, wie er im Vertrag steht (Ergebnis der Extraktion)."""
    vertrag: str
    kennzahl: str                    # Bezeichnung laut Vertrag
    typ: KennzahlTyp
    operator: Literal[">=", "<="]
    grenzwert: float                 # in Prozent bei Quoten, sonst Faktor
    rundung_stellen: int | None      # None = Vertrag regelt keine Rundung
    fundstelle_grenzwert: str
    fundstelle_definition: str
    definition_kurz: str
    parameter: dict = field(default_factory=dict)


@dataclass
class Ergebnis:
    covenant: Covenant
    wert: float                      # ggf. gerundet laut Vertrag
    wert_ungerundet: float
    headroom: float
    eingehalten: bool
    komponenten: list[Wert]          # Zähler/Nenner usw. mit Quellen
    rechenweg: str
    puffer: str                      # wie viel Spielraum in Euro/Prozent


# ---------- Bausteine ----------

def ebitda(plan: Plan, p: dict) -> Wert:
    """EBITDA laut Vertragsdefinition (V3 Anl. 3 Abs. 2 / V4 § 1 sind identisch aufgebaut):
    JÜ + Ertragsteuern + Zinsaufwand − Zinserträge + Abschreibungen ± a.o. Posten.
    Der Wirtschaftsplan weist keine außerordentlichen Posten (§ 285 Nr. 31 HGB) aus."""
    zins = plan.zinsaufwand_guv
    if not p.get("ebitda_aufzinsung_einbeziehen", True):
        zins = zins - plan.aufzinsung_rueckstellungen
    return (plan.jahresueberschuss + plan.steuern_ertrag + zins
            - plan.zinsertraege + plan.abschreibungen).benannt("EBITDA")


def _finanzdarlehen(plan: Plan, gesellschafterdarlehen: bool, nur_nicht_nachrangige: bool = False):
    for d in plan.darlehen:
        if d.gesellschafterdarlehen:
            if not gesellschafterdarlehen:
                continue
            if nur_nicht_nachrangige and d.nachrangig:
                continue
        yield d


# ---------- Kennzahlen ----------

def eigenkapitalquote(plan: Plan, p: dict) -> tuple[float, list[Wert], str]:
    anteil = float(p.get("sonderposten_anteil", 0.0))
    ek = plan.eigenkapital.benannt("Eigenkapital (HGB § 266 Abs. 3 A)")
    teile = [ek]
    zaehler = ek
    if anteil:
        sp = plan.sonderposten.mal(anteil).benannt(f"{anteil:.0%} der Sonderposten")
        teile.append(sp)
        zaehler = (ek + sp).benannt("Wirtschaftliches Eigenkapital")
        teile.append(zaehler)
    bs = plan.bilanzsumme.benannt("Bilanzsumme")
    teile.append(bs)
    quote = zaehler.betrag / bs.betrag * 100
    if anteil:
        weg = (f"({_t(ek.betrag)} + {anteil:.0%} × {_t(plan.sonderposten.betrag)}) / {_t(bs.betrag)}"
               f" = {_t(zaehler.betrag)} / {_t(bs.betrag)} = {quote:.2f} %")
    else:
        weg = f"{_t(ek.betrag)} / {_t(bs.betrag)} = {quote:.2f} % (ohne Sonderposten)"
    return quote, teile, weg


def dyn_verschuldungsgrad(plan: Plan, p: dict) -> tuple[float, list[Wert], str]:
    mit_nachrangigen = bool(p.get("nachrangige_gesellschafterdarlehen_einbeziehen", False))
    darlehen = list(_finanzdarlehen(plan, gesellschafterdarlehen=True,
                                    nur_nicht_nachrangige=not mit_nachrangigen))
    fv = summe([d.stand_ende for d in darlehen]).benannt("Finanzverbindlichkeiten 31.12.")
    lm = plan.liquide_mittel.benannt("Liquide Mittel")
    nfv = (fv - lm).benannt("Nettofinanzverbindlichkeiten")
    e = ebitda(plan, p)
    grad = nfv.betrag / e.betrag
    ausgeschlossen = [d.name for d in plan.darlehen if d not in darlehen]
    weg = (f"({_t(fv.betrag)} − {_t(lm.betrag)}) / {_t(e.betrag)} = {grad:.3f}"
           + (f"; nicht enthalten: {', '.join(ausgeschlossen)}" if ausgeschlossen else ""))
    return grad, [fv, lm, nfv, e], weg


def dscr(plan: Plan, p: dict) -> tuple[float, list[Wert], str]:
    mit_gesell = bool(p.get("gesellschafterdarlehen_einbeziehen", True))
    mit_sonder = bool(p.get("sondertilgung_einbeziehen", False))
    darlehen = list(_finanzdarlehen(plan, gesellschafterdarlehen=mit_gesell))

    e = ebitda(plan, p)
    st = plan.steuern_ertrag.benannt("Ertragsteuern")
    zaehler = (e - st).benannt("EBITDA − Ertragsteuern")

    # Zinsen je Darlehen aus dem Darlehensspiegel – dort bereits ohne Aufzinsung von Rückstellungen.
    zins = summe([d.zinsaufwand for d in darlehen]).benannt("Zinsaufwand Finanzverbindlichkeiten")
    tilg = summe([d.planm_tilgung for d in darlehen]).benannt("Planmäßige Tilgung")
    nenner_teile = [zins, tilg]
    if mit_sonder:
        nenner_teile.append(summe([d.sondertilgung for d in darlehen]).benannt("Sondertilgungen"))
    nenner = summe(nenner_teile).benannt("Schuldendienst")
    wert = zaehler.betrag / nenner.betrag

    sonder_ges = sum(d.sondertilgung.betrag for d in plan.darlehen)
    weg = (f"({_t(e.betrag)} − {_t(st.betrag)}) / ({_t(zins.betrag)} Zins + {_t(tilg.betrag)} Tilgung"
           + (f" + Sondertilgung" if mit_sonder else "") + f") = {_t(zaehler.betrag)} / "
           f"{_t(nenner.betrag)} = {wert:.3f}"
           + ("" if mit_sonder or not sonder_ges else
              f"; Sondertilgung {_t(sonder_ges)} und Aufzinsung "
              f"{_t(plan.aufzinsung_rueckstellungen.betrag)} nicht enthalten"))
    return wert, [e, st, zaehler, zins, tilg, nenner], weg


RECHNER = {
    "eigenkapitalquote": eigenkapitalquote,
    "dyn_verschuldungsgrad": dyn_verschuldungsgrad,
    "dscr": dscr,
}


def pruefe(plan: Plan, cov: Covenant) -> Ergebnis:
    roh, teile, weg = RECHNER[cov.typ](plan, cov.parameter)
    wert = kaufm_runden(roh, cov.rundung_stellen) if cov.rundung_stellen is not None else roh
    if cov.operator == ">=":
        headroom, ok = wert - cov.grenzwert, wert >= cov.grenzwert
    else:
        headroom, ok = cov.grenzwert - wert, wert <= cov.grenzwert
    return Ergebnis(cov, wert, roh, headroom, ok, teile, weg, _puffer(cov, roh, teile))


def _puffer(cov: Covenant, roh: float, teile: list[Wert]) -> str:
    """Übersetzt den Headroom in eine greifbare Größe für die Finanzleitung."""
    g = cov.grenzwert
    if cov.typ == "eigenkapitalquote":
        bs = teile[-1].betrag
        luft = (roh - g) / 100 * bs
        return f"Eigenkapital darf um rd. {_t(luft)} T€ niedriger ausfallen (bei gleicher Bilanzsumme)"
    if cov.typ == "dyn_verschuldungsgrad":
        e = teile[-1].betrag
        nfv = teile[2].betrag
        min_ebitda = nfv / g
        return (f"EBITDA darf um rd. {_p(1 - min_ebitda / e)} ({_t(e - min_ebitda)} T€) sinken "
                f"oder Nettoverschuldung um rd. {_t(g * e - nfv)} T€ steigen")
    if cov.typ == "dscr":
        z, n = teile[2].betrag, teile[-1].betrag
        return f"EBITDA − Steuern darf um rd. {_p(1 - g * n / z)} ({_t(z - g * n)} T€) sinken"
    return ""


def _t(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


def _p(anteil: float) -> str:
    """0.168 → '16,8 %' (deutsche Schreibweise)."""
    return f"{anteil * 100:.1f}".replace(".", ",") + " %"
