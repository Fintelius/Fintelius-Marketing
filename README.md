# Fintelius Marketing Module
Daily: reads inbox (replies/bounces) -> researches projects and prospects -> finds published contacts -> drafts intro emails -> emails you a report.
Mode `review` (default): nothing is sent until you run `python run.py approve --all` (or `--id N`) and then `python run.py send`.
Mode `auto` (set SEND_MODE=auto in .env): drafts passing every guard rule are sent on the daily run.
Safety: no prices/commitments, warm-up caps, weekday 09:30-17:30 IST, skips generic mailboxes, do-not-contact list, auto-stop on bounces (HALT file).
Commands: init, check, status, import-csv FILE, research, prepare, show ID, approve, reject ID, send [--dry-run], inbox, daily, stop, resume.
Vendor registration is only prepared (checklist in the daily report); a person completes the portal forms.
