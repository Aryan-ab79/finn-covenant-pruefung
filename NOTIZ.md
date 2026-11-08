# Notiz zur Case Study „Finn“

**Annahmen**
- Stichtag 31.12.2027, Werte in T€. Kennzahlen auf Planbasis dienen der Frühwarnung (so auch V3 Anl. 3 Abs. 7, V4 § 14 Abs. 3).
- Der Wirtschaftsplan weist keine außerordentlichen Posten (§ 285 Nr. 31 HGB) aus; EBITDA daher ohne a. o. Bereinigung.
- Das Gesellschafterdarlehen der Stadt erfüllt die Merkmale „Nachrangiges Gesellschafterdarlehen“ in V4 (qualifizierter Rangrücktritt nach § 39 Abs. 2 InsO, Zahlung nur aus freiem Vermögen – Erläuterung 2 der Planbilanz).
- Altdarlehen: laut Erläuterung ohne Covenants; die Verträge lagen nicht vor.

**Die entscheidenden Abgrenzungen** – dieselbe Position wird je Vertrag anders behandelt:
- *Sparkasse:* Die Präambel nennt EK-Quote und Verschuldungsgrad nur als Grundlage der Kreditentscheidung → **kein** Covenant.
- *Gesellschafterdarlehen:* zählt beim DSCR der Landesbank mit (Anl. 3 Abs. 3 d), beim Verschuldungsgrad des Konsortiums nicht (§ 1). Falsch gelesen: 3,57 statt 3,37 → scheinbarer Covenant-Bruch.
- *Sondertilgung 3.000* und *Aufzinsung 170* gehören nicht in den Schuldendienst (Anl. 3 Abs. 4) → DSCR 1,56 statt 1,39.
- *Sonderposten:* zu 50 % im Wirtschaftlichen EK der Förderbank, beim Konsortium gar nicht.

**Was ich an der Vorgabe geändert habe – und warum**
1. **Belege werden im Code geprüft, nicht geglaubt.** Das LLM muss jede Abgrenzung mit einem wörtlichen Zitat belegen; der Code sucht das Zitat im PDF und bestimmt die Seite selbst. Erfundene Zitate führen zu einer Warnung in der Mail.
2. **Vertragsauswertung wird gecacht und hat eine freigegebene Rückfallebene.** Verträge ändern sich selten; in der Praxis würde eine Fachkraft die Extraktion einmal freigeben (Vier-Augen-Prinzip). Fällt die LLM-API aus, rechnet Finn mit der freigegebenen Auswertung und sagt das in der Mail.
3. **Mehr als „eingehalten ja/nein“:** Ampel „knapp“ (Headroom < 10 % des Grenzwerts), Puffer in T€ („EBITDA darf um 3,6 % sinken“), automatisch gerechnete Gegenprobe je Abgrenzung, Margenraster (3,37 → 1,25 % statt 0,95 %) und Berichtsfristen (Konsortium: 30 Tage nach Aufstellung → **14.11.2026**; Landesbank: 15.12.2026).
4. **Prüfpfad als JSON-Anhang**, damit das Ergebnis maschinell weiterverarbeitet oder revisionssicher abgelegt werden kann.
5. **Zahlen-Wächter:** Formuliert das LLM die Zusammenfassung, wird jede Zahl darin gegen die Code-Ergebnisse geprüft; sonst greift eine Textvorlage.

**Wie ich KI eingesetzt habe**
- Beim Bauen: Claude als Pair-Programmer für Gerüst, Parser und Mail-Logik. Die Vertragsauswertung (Referenz) und die Sollwerte habe ich selbst aus den Verträgen hergeleitet und als Tests festgeschrieben – daran wird der generierte Code gemessen.
- Im Agenten: Claude (Tool Use mit festem JSON-Schema) liest die Verträge und formuliert die Zusammenfassung. Rechnen: ausschließlich Python.
- Kontrolle: Im Live-Lauf hat das LLM alle Covenants richtig erkannt (inkl. „Sparkasse: keiner“), alle 4 Kennzahlen stimmen mit meinen Sollwerten überein, 46 von 46 Belegzitaten wurden vom Code wörtlich im Vertrag gefunden. Mit `python -m finn.eval` lässt sich das wiederholt gegen die Referenz messen.

**Was beim Bauen schiefging (und gelöst ist)**
- Das aktuelle Claude-Modell erlaubt kein erzwungenes Werkzeug (`tool_choice`) und verlangt bei großen Antworten Streaming → umgestellt auf `tool_choice=auto` + Streaming. Bis dahin hat die Rückfallebene korrekt übernommen und es in der Mail ausgewiesen.

**Was ich mit mehr Zeit als Nächstes bauen würde**
- Wiederholte Messung der Extraktion (`finn.eval`) als fester Bestandteil vor jeder Vertragsänderung.
- Freigabe-Workflow per Mail („Finn, Auswertung V4 freigeben“) statt JSON-Datei.
- Neue Kennzahltypen über eine kleine Formelsprache statt fester Funktionen (z. B. Zinsdeckungsgrad).
- Sensitivitäten auf Planannahmen (EBITDA −10 %, Investitionen +20 %) und Mehrjahresvorschau.
- Betrieb als Dienst (Docker, Gmail-API mit Push statt Polling), Auftragsbestätigung bei langen Läufen.
