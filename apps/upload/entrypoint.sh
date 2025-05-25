#!/bin/bash
printenv | sed 's/^/export /' > /tmp/cron-env
cron
exec uvicorn main:app --host 0.0.0.0 --port 8080 --log-level debug
