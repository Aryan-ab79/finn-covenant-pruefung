# Finn – Covenant-Prüfung per E-Mail

Finn ist ein digitaler Referent Finanzierung. Die Leitung Finanzen schickt ihm einen Wirtschaftsplan per Mail,
Finn prüft die Financial Covenants aus den Kreditverträgen und antwortet mit einem prüfbaren Ergebnis:
Wert, Grenzwert, Headroom, eingehalten ja/nein – und zu jeder Zahl die Fundstelle im Vertrag und im Wirtschaftsplan.

**Grundsatz:** Das Sprachmodell liest, strukturiert und formuliert. Jede Kennzahl entsteht im Code (`finn/rechnen.py`).

## Start in vier Befehlen

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # API-Key und Postfach eintragen
python -m finn                                           # Postfach überwachen
```

Dann eine Mail mit dem Wirtschaftsplan (`.xlsx`) als Anhang an die Adresse in `FINN_EMAIL` schicken,
Betreff z. B. „Covenant-Prüfung Wirtschaftsplan 2027“. Antwort kommt nach ca. 1 Minute (beim ersten Lauf, danach schneller durch den Vertrags-Cache).

| Befehl | Zweck |
|---|---|
| `python -m finn --einmal` | Postfach einmal abrufen |
| `python -m finn --datei data/Wirtschaftsplan_2027.xlsx` | Ohne Mail rechnen, Ergebnis in `out/antwort.html` |
| `python -m finn --eml beispiele/auftrag.eml` | Auftrag aus `.eml`-Datei, Antwort in `out/antwort.eml` |
| `python -m finn --referenz …` | Freigegebene Vertragsauswertung statt LLM (Notfallmodus) |
| `python -m finn.eval --laeufe 5` | Zuverlässigkeit der LLM-Extraktion gegen die Referenz messen |
| `pytest` | Rechenkern und Zitatprüfung testen (offline) |

**Gmail:** Bestätigung in zwei Schritten aktivieren, dann unter myaccount.google.com/apppasswords ein
App-Passwort erzeugen und als `FINN_EMAIL_PASSWORT` eintragen. Mails von beliebigen Absendern werden angenommen.

## Ablauf

```
Mail (IMAP) → Wirtschaftsplan lesen + Abgleiche → Verträge: LLM-Extraktion → Zitatprüfung im Code
            → Kennzahlen je Vertragsdefinition rechnen → Abgrenzungen/Fristen/Margen → Antwort (SMTP)
```

| Modul | Aufgabe |
|---|---|
| `wirtschaftsplan.py` | Liest Positionen über Zeilentexte (nicht feste Zellen), rechnet Summen nach, gleicht Bilanz ↔ Darlehensspiegel ↔ GuV ab |
| `extraktion.py` | LLM füllt ein festes Schema (Tool Use) inkl. wörtlicher Belegzitate; Cache je PDF-Hash; Rückfall auf freigegebene Auswertung |
| `pdf.py` | Prüft jedes Zitat gegen den PDF-Text, bestimmt die Seitenzahl selbst, prüft Grenzwert im Zitat |
| `rechnen.py` | Eine Funktion je Kennzahltyp; Vertragsunterschiede als Parameter; kaufmännisches Runden laut Vertrag |
| `bericht.py` | Mail: Übersicht, Rechenweg mit Fundstellen, Abgrenzungen, Margen und Fristen, Prüfhinweise; Zahlen-Wächter für den LLM-Text |
| `postfach.py` | IMAP-Polling, Antwort im Thread (In-Reply-To), idempotent, keine Mail-Schleifen |

## Ergebnis Plan 2027

| Vertrag | Covenant | Plan 2027 | Grenze | Headroom |
|---|---|---|---|---|
| Sparkasse | – keine Financial Covenants (Bürgschaft der Stadt) | | | |
| Förderbank | Wirtschaftliche EK-Quote | 34,32 % | ≥ 27,5 % | +6,82 Pp. |
| Landesbank | DSCR | 1,56 | ≥ 1,30 | +0,26 |
| Konsortium | EK-Quote | 30,7 % | ≥ 30,0 % | +0,7 Pp. (knapp) |
| Konsortium | Dyn. Verschuldungsgrad | 3,37 | ≤ 3,50 | +0,13 (knapp) |

Details zu Annahmen und Entscheidungen: siehe `NOTIZ.md`.
