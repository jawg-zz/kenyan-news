#!/usr/bin/env bash
# Kenyan News Daily Briefing — cron wrapper
# Delivers a markdown briefing to Telegram.
set -e
cd /opt/data/workspace/kenyan-news
export VIRTUAL_ENV=/opt/data/workspace/kenyan-news/.venv
export PATH=$VIRTUAL_ENV/bin:$PATH
exec python3 -m kenyan_news.__main__ --briefing --briefing-md
