#!/bin/bash

# Load secrets (Fly injects them at runtime into ENV)
export R2_ENDPOINT="$R2_ENDPOINT"
export R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export R2_BUCKET="$R2_BUCKET"

/usr/local/bin/python3 /app/sync-to-r2.py >> /var/log/cron.log 2>&1
