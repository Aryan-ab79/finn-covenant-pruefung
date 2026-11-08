"""Prüft die Verifikation der LLM-Ausgabe offline (ohne API-Aufruf)."""
from pathlib import Path

from finn.extraktion import pruefe_auswertung
from finn.pdf import lese_seiten
from finn.rechnen import pruefe
from finn.wirtschaftsplan import lade_plan

DATA = Path(__file__).parent.parent / "data"
V4 = str(DATA / "vertraege" / "Vertrag_4_Konsortialkredit.pdf")


def _z(seite, text):
    return {"seite": seite, "text": text}


def _llm_ausgabe_v4(dyn_param_zitat: str):
    return {
        "vertrag": "Konsortialkredit EUR 64 Mio.",
        "hat_financial_covenants": True,
        "begruendung": "§ 14 enthält zwei Finanzkennzahlen.",
        "covenants": [{
            "kennzahl": "Dynamischer Verschuldungsgrad",
            "typ": "dyn_verschuldungsgrad", "operator": "<=", "grenzwert": 3.5, "rundung_stellen": 2,
            "paragraph_grenzwert": "§ 14 Abs. 1 lit. b", "paragraph_definition": "§ 1 Abs. 1",
            # absichtlich falsche Seite (6 statt 7) – der Code soll korrigieren
            "zitat_grenzwert": _z(6, "der Dynamische Verschuldungsgrad für jeden Prüfungszeitraum nicht mehr als 3,50 beträgt"),
            "zitate_definition": [_z(2, "das Verhältnis der Nettofinanzverbindlichkeiten zum letzten Tag des Prüfungszeitraums zum EBITDA")],
            "definition_kurz": "NFV / EBITDA",
            "parameter": {
                "nachrangige_gesellschafterdarlehen_einbeziehen": {"wert": False, "beleg": _z(2, dyn_param_zitat)},
                "ebitda_aufzinsung_einbeziehen": {"wert": True, "beleg": _z(2, "(b) zuzüglich der Zinsen und ähnlichen Aufwendungen")},
            },
        }],
        "pflichten_planjahr": [],
        "margenraster": None,
    }


def test_echte_zitate_werden_verifiziert_und_seite_korrigiert():
    roh = _llm_ausgabe_v4("jedoch jeweils mit Ausnahme von Nachrangigen Gesellschafterdarlehen")
    a = pruefe_auswertung(roh, lese_seiten(V4), "V4.pdf")
    assert a.zitate_ok == a.zitate_geprueft == 4
    assert not [b for b in a.befunde if b.ebene == "warnung"]
    cov = a.covenants[0]
    assert cov.fundstelle_grenzwert == "§ 14 Abs. 1 lit. b, S. 7"     # Seite vom Code, nicht vom LLM
    e = pruefe(lade_plan(DATA / "Wirtschaftsplan_2027.xlsx"), cov)
    assert e.wert == 3.37


def test_erfundenes_zitat_wird_erkannt():
    roh = _llm_ausgabe_v4("Gesellschafterdarlehen werden bei der Berechnung nie berücksichtigt")
    a = pruefe_auswertung(roh, lese_seiten(V4), "V4.pdf")
    assert a.zitate_ok == 3
    texte = " ".join(b.text for b in a.befunde)
    assert "nicht im Vertrag gefunden" in texte and "ohne gültigen Beleg" in texte


def test_grenzwert_muss_im_zitat_stehen():
    roh = _llm_ausgabe_v4("jedoch jeweils mit Ausnahme von Nachrangigen Gesellschafterdarlehen")
    roh["covenants"][0]["grenzwert"] = 3.0          # LLM verliest sich
    a = pruefe_auswertung(roh, lese_seiten(V4), "V4.pdf")
    assert any("steht nicht im Zitat" in b.text for b in a.befunde)
