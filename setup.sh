#!/bin/bash
# Run once on the server from ~/marketing/app :  bash setup.sh
set -e
cd "$(dirname "$0")"
source ../venv/bin/activate
pip install -q -r requirements.txt
python run.py init
python run.py check
# Daily run: weekdays 10:00 IST = 04:30 UTC
CRON="30 4 * * 1-5 cd $HOME/marketing/app && $HOME/marketing/venv/bin/python run.py daily >> $HOME/marketing/data/daily.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'run.py daily' ; echo "$CRON" ) | crontab -
echo "Daily run installed. Mode: ${SEND_MODE:-review (from .env)}"
