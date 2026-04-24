Status: Approved by user on 2026-04-24. Ready for implementation handoff.


## Plan: Jiffy Monats-Export nach 2 CSV
Erstellung eines Python-CLI-Skripts, das die Jiffy-JSON einliest, einen Monat (`YYYY-MM`) verarbeitet und zwei CSVs erzeugt: 1) ADEBAR-zentrierte Einzelzeilen mit Viertelstunden-Rundung und Pausen-Spalte, 2) tagesbasierte Gesamtsicht mit Tagesstart/-ende und summierten Stunden je Einzelprojekt. Pausenregeln werden tagweise auf alle Projekte angewendet (inkl. automatischer Ergänzung), damit die Nettozeiten den gewünschten Grenzen folgen.

**Steps**
1. Discovery der finalen Datenquellen im JSON und Feld-Mapping festlegen: `time_entries` als Buchungen, `time_owners` als Owner/Projekt-Hierarchie (`id`, `name`, `parent_id`), Zeitfelder `start_time`/`stop_time`, Notizfeld `note`, Statusfilter (`ACTIVE` einbeziehen, `DELETED` ausschließen).  
*blocks all following steps*
2. CLI-Design und I/O-Verhalten definieren: Pflichtparameter `--month YYYY-MM`, optional `--input` (Default: erste JSON im Arbeitsverzeichnis), optionale `--output-dir`; feste CSV-Separatoren `;`, UTF-8, deutsche Zahlenformatierung für Stunden mit Komma.  
*depends on 1*
3. Zeitnormalisierung implementieren: Unix-Millis in lokale Zeit (`Europe/Berlin`) umwandeln, Monatsfilter auf lokale Datumskomponente, je Eintrag Start abrunden auf 15 Minuten und Ende aufrunden auf 15 Minuten, Dauer aus gerundeten Zeiten neu berechnen.  
*depends on 1*
4. Pausenlogik je Tag aufbauen (über alle Projekte): explizite Pausen aus Einträgen mit Owner-Name `Pause` erkennen; erforderliche Mindestpause aus Tages-Netto bestimmen (`<=6h: 0`, `>6h..9h: 0:30`, `>9h: 0:45`); falls Netto nach Abzug noch `>10:00h`, zusätzliche Pause auffüllen bis netto `10:00h`; fehlende Pausen möglichst um `12:00` als synthetische Pause einfügen und im Log vermerken.  
*depends on 3*
5. CSV 1 (ADEBAR) erzeugen: nur Einträge, deren Owner `ADEBAR` oder ein Kind von `ADEBAR` ist; Ausgabezeilen: `Datum (DD.MM.YYYY); Uhrzeit von; Uhrzeit bis; Pause; Dauer (Komma-Stunden); Beschreibung`. Beschreibung aus Unterprojektname + optionaler Notiz aufbauen. `Pause` als vorangehende Tageslücke/zugewiesene Pause vor dem Eintrag ausgeben (nach angewandter Pausenlogik).  
*depends on 3,4*
6. CSV 2 (tagbasiert, alle Projekte) erzeugen: eine Zeile pro Tag mit `Datum`, Tagesanfang, Tagesende und dynamischen Spalten je Einzelprojekt (Projektname), Werte als aufsummierte Komma-Stunden aus gerundeten Intervallen; Projektspalten stabil sortieren (alphabetisch).  
*depends on 3,4*
7. Robustheit und Doku ergänzen: englische Kommentare nur an komplexen Stellen, englische Modul-/CLI-Doku, klare Fehlermeldungen (Monatsformat, leere Treffer, fehlende JSON-Schlüssel), optionales Logging für synthetische Pausen und >10h-Korrekturen.  
*parallel with step 5/6 once core logic exists*
8. Verifikation durchführen: Script für mindestens einen Monat laufen lassen, CSV-Struktur prüfen, Spot-Checks auf Rundung (von runter/bis rauf), Pausenanwendung und Summenabgleich zwischen Detail- und Tagessicht dokumentieren.  
*depends on 5,6,7*

**Relevant files**
- `c:/Users/marco.volkert/Documents/Repos/jiffy-to-csv/jiffy-SM-G991B-1776964074.json` — Quelle für `time_entries` und `time_owners`; Referenz für Owner-Hierarchie (`ADEBAR` + Unterprojekte) und Pausen-Owner `Pause`.
- Neuer Python-CLI-Exporter im Repository-Root — Implementierung des Monatsfilters, Rundungs-/Pausenlogik und beider CSV-Exporte.

**Verification**
1. Script mit einem konkreten Monat starten (`YYYY-MM`) und bestätigen, dass zwei CSV-Dateien erzeugt werden.
2. Stichprobe von 5-10 Zeilen in CSV 1: Startzeiten sind 15-Minuten-floor, Endzeiten 15-Minuten-ceil, Dauer entspricht gerundeter Differenz.
3. Tagesstichprobe mit/ohne explizite Pause: Mindestpausenregel korrekt angewandt, bei >10h Netto wird zusätzliche Pause gesetzt und geloggt.
4. CSV 2 prüfen: genau eine Zeile pro Tag im Monat, Tagesanfang/-ende plausibel, Projektspalten vollständig und stabil sortiert.
5. Summenabgleich: Tages-Gesamtsumme aus CSV 1 (ADEBAR-Scope) gegen entsprechende Projektsummen in CSV 2 nachvollziehen; Differenzen nur bei bewusst unterschiedlichem Scope (alle Projekte vs ADEBAR-only).

**Decisions**
- Monat wird als CLI-Parameter `YYYY-MM` übergeben.
- Dauer wird immer aus den gerundeten Zeiten berechnet.
- Pausenregel gilt tagweise über alle Projekte, nicht nur ADEBAR.
- CSV 2 ist tagesbasiert mit dynamischen Projektspalten (Pivot-Ansatz).
- Scope-Unterschied bleibt bewusst: CSV 1 = ADEBAR(+Unterprojekte), CSV 2 = alle Projekte.

**Further Considerations**
1. Synthetic-Pause-Zuordnung bei dichtem Tagesplan: bevorzugt in der größten Lücke um 12:00; falls keine Lücke vorhanden, am Tagesende anhängen und im Log kennzeichnen.
2. Owner-Namenskonflikte (gleichnamige Projekte): intern über Owner-ID aggregieren, in CSV-Spaltenname bei Kollision optional mit ID-Suffix auflösen.