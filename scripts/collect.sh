#!/usr/bin/env bash
# Kenyan News Collector — cron wrapper
# Activates the project venv and runs the collection script.
set -e
cd /opt/data/workspace/kenyan-news
export VIRTUAL_ENV=/opt/data/workspace/kenyan-news/.venv
export PATH=$VIRTUAL_ENV/bin:$PATH
exec python3 /opt/data/workspace/kenyan-news/scripts/collect.py
