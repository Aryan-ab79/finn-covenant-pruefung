"""Sollwerte von Hand nachgerechnet (Plan 2027, Werte in T€).

EBITDA = 10.700 JÜ + 3.200 Steuern + 5.100 Zinsaufwand − 300 Zinserträge + 21.500 AfA = 40.200
"""
import json
from pathlib import Path

import pytest

from finn.rechnen import Covenant, ebitda, pruefe
from finn.werte import kaufm_runden
from finn.wirtschaftsplan import lade_plan

DATA = Path(__file__).parent.parent / "data"


@pytest.fixture(scope="module")
def plan():
    return lade_plan(DATA / "Wirtschaftsplan_2027.xlsx")


@pytest.fixture(scope="module")
def referenz():
    daten = json.loads((DATA / "covenants_referenz.json").read_text(encoding="utf-8"))
    return {(v["datei"][:10], c["typ"]): Covenant(vertrag=v["vertrag"], **c)
            for v in daten["vertraege"] for c in v["covenants"]}


def test_plan_basiswerte(plan):
    assert plan.planjahr == 2027
    assert plan.jahresueberschuss.betrag == 10_700
    assert plan.eigenkapital.betrag == 96_100
    assert plan.bilanzsumme.betrag == 313_200
    assert plan.sonderposten.betrag == 22_800
    assert plan.liquide_mittel.betrag == 12_600
    assert plan.warnungen == []           # alle Abgleiche im Excel gehen auf
    assert len(plan.darlehen) == 6


def test_ebitda(plan):
    assert ebitda(plan, {}).betrag == 40_200


def test_v2_wirtschaftliche_ek_quote(plan, referenz):
    e = pruefe(plan, referenz[("Vertrag_2_", "eigenkapitalquote")])
    assert e.wert_ungerundet == pytest.approx(107_500 / 313_200 * 100)   # 34,32 %
    assert e.eingehalten and e.headroom == pytest.approx(6.82, abs=0.01)


def test_v3_dscr(plan, referenz):
    e = pruefe(plan, referenz[("Vertrag_3_", "dscr")])
    # Zähler 40.200 − 3.200 = 37.000; Nenner 4.930 Zins + 18.750 planm. Tilgung = 23.680
    assert e.wert_ungerundet == pytest.approx(37_000 / 23_680)
    assert e.wert == 1.56 and e.eingehalten
    assert e.headroom == pytest.approx(0.26)


def test_v4_ek_quote(plan, referenz):
    e = pruefe(plan, referenz[("Vertrag_4_", "eigenkapitalquote")])
    assert e.wert == 30.7 and e.eingehalten
    assert e.headroom == pytest.approx(0.7)


def test_v4_dyn_verschuldungsgrad(plan, referenz):
    e = pruefe(plan, referenz[("Vertrag_4_", "dyn_verschuldungsgrad")])
    # (148.200 − 12.600) / 40.200, Gesellschafterdarlehen (nachrangig) ausgenommen
    assert e.wert_ungerundet == pytest.approx(135_600 / 40_200)
    assert e.wert == 3.37 and e.eingehalten
    assert e.headroom == pytest.approx(0.13)


# --- Die typischen Fehler: zeigt, dass die Parameter wirklich wirken ---

def test_falle_gesellschafterdarlehen_bei_v4_wuerde_bruch_zeigen(plan, referenz):
    falsch = referenz[("Vertrag_4_", "dyn_verschuldungsgrad")]
    falsch = Covenant(**{**falsch.__dict__, "parameter": {"nachrangige_gesellschafterdarlehen_einbeziehen": True}})
    assert pruefe(plan, falsch).wert == 3.57 and not pruefe(plan, falsch).eingehalten


def test_falle_sondertilgung_bei_v3(plan, referenz):
    falsch = referenz[("Vertrag_3_", "dscr")]
    falsch = Covenant(**{**falsch.__dict__, "parameter": {**falsch.parameter, "sondertilgung_einbeziehen": True}})
    assert pruefe(plan, falsch).wert == 1.39


def test_kaufmaennisch_runden():
    assert kaufm_runden(2.345, 2) == 2.35      # round() in Python ergäbe 2.34 bzw. 2.35 je nach Float
    assert kaufm_runden(30.65, 1) == 30.7
