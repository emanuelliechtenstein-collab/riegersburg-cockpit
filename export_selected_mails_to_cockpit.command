#!/bin/bash
set -e

cd "$(dirname "$0")"

osascript <<'APPLESCRIPT'
property targetFolder : "/Users/rauby/Desktop/Fortbestand der Riegerbsurg /Apple Mail Import"

do shell script "mkdir -p " & quoted form of targetFolder

tell application "Mail"
	set selectedMessages to selection
	if selectedMessages is {} then
		display dialog "Bitte zuerst eine oder mehrere E-Mails in Apple Mail markieren." buttons {"OK"} default button "OK"
		return
	end if
	
	repeat with selectedMessage in selectedMessages
		set mailSubject to subject of selectedMessage
		set mailSender to sender of selectedMessage
		set mailDate to date received of selectedMessage
		set mailContent to content of selectedMessage
		set localMailId to id of selectedMessage as text
		
		set cleanSubject to my sanitizeText(mailSubject)
		if cleanSubject is "" then set cleanSubject to "Ohne Betreff"
		
		set timestampValue to do shell script "date '+%Y-%m-%d_%H-%M-%S'"
		set filePath to targetFolder & "/" & timestampValue & "_Mail_" & localMailId & "_" & cleanSubject & ".txt"
		
		set exportText to "Quelle: Apple Mail" & linefeed & ¬
			"Apple-Mail-ID: " & localMailId & linefeed & ¬
			"Betreff: " & mailSubject & linefeed & ¬
			"Von: " & mailSender & linefeed & ¬
			"Datum: " & (mailDate as text) & linefeed & ¬
			"Importiert am: " & (current date as text) & linefeed & linefeed & ¬
			mailContent
		
		my writeUtf8File(filePath, exportText)
	end repeat
end tell

display dialog "Die markierten E-Mails wurden für das Riegersburg-Cockpit abgelegt." buttons {"OK"} default button "OK"

on sanitizeText(inputText)
	set pythonCode to "
import re
import sys
text = sys.stdin.read()
text = re.sub(r'[^A-Za-z0-9ÄÖÜäöüß _.-]+', '_', text)
text = re.sub(r'\\s+', ' ', text).strip()
print(text[:80])
"
	set shellCommand to "printf %s " & quoted form of inputText & " | python3 -c " & quoted form of pythonCode
	return do shell script shellCommand
end sanitizeText

on writeUtf8File(posixPath, textValue)
	set tempFile to POSIX file posixPath
	set fileHandle to open for access tempFile with write permission
	try
		set eof of fileHandle to 0
		write textValue to fileHandle as «class utf8»
		close access fileHandle
	on error errorMessage
		try
			close access fileHandle
		end try
		error errorMessage
	end try
end writeUtf8File
APPLESCRIPT

.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python sync_desktop_folder.py

echo ""
read -n 1 -s -r -p "Fertig. Zum Schließen eine Taste drücken."
