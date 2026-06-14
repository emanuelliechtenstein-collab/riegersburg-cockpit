# Automatischer Mail-Import in das Riegersburg-Cockpit

Diese Erweiterung verbindet Apple Mail mit dem bestehenden Riegersburg-Sync.

## Was passiert?

- Apple Mail erkennt passende E-Mails über eine Mail-Regel.
- Die E-Mail wird als Textdatei im Ordner `Fortbestand der Riegerbsurg /Apple Mail Import` abgelegt.
- Der vorhandene Sync-Helfer lädt die Datei in das Cockpit.
- Bereits übernommene Mails werden über eine Mail-Kennung nicht doppelt hochgeladen.

## Installation

1. `install_mail_rule_script.command` doppelklicken.
2. Falls macOS fragt, Zugriff erlauben.
3. Apple Mail öffnen.
4. In Mail: `Mail` > `Einstellungen` > `Regeln`.
5. `Regel hinzufügen` anklicken.
6. Name: `Riegersburg Cockpit`.
7. Bedingung zum Beispiel:
   - `Von` `enthält` `Wagenhofer`
   - oder `Betreff` `enthält` `Riegersburg`
8. Aktion:
   - `AppleScript ausführen`
   - `Riegersburg Mail Import.scpt` auswählen.
9. Regel speichern.

## Empfehlung

Am Anfang die Regel eher eng halten, zum Beispiel nur für bekannte Projektkontakte oder Betreffzeilen mit `Riegersburg`.
So landen keine privaten oder fremden E-Mails im Cockpit.

## Manuelle Ausweichmöglichkeit

Falls Apple Mail die automatische Regel nicht sofort anbietet:

1. In Apple Mail eine oder mehrere E-Mails markieren.
2. `export_selected_mails_to_cockpit.command` doppelklicken.
3. macOS-Zugriff erlauben.
4. Die markierten Mails werden abgelegt und direkt synchronisiert.
