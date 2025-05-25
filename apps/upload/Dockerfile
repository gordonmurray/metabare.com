FROM python:3.9-slim

WORKDIR /app

# System deps for pip + PIL
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install deps first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Preload model + processor
RUN python -c "from transformers import CLIPProcessor, CLIPModel; CLIPModel.from_pretrained('openai/clip-vit-base-patch32'); CLIPProcessor.from_pretrained('openai/clip-vit-base-patch32')"

# Copy the app files
COPY main.py vectorize.py storage.py sync-to-r2.py ./

# Copy the cron job
COPY metabare-cron /etc/cron.d/metabare-cron

# Copy the run-sync script
COPY run-sync.sh /app/run-sync.sh

# Make sure the cron job is executable
RUN chmod +x /app/run-sync.sh && chmod 0644 /etc/cron.d/metabare-cron && crontab /etc/cron.d/metabare-cron

# Make sure the run-sync script is executable
RUN chmod +x /app/run-sync.sh

# Expose the port the app runs on
EXPOSE 8080

# Copy the entrypoint script
COPY entrypoint.sh /app/entrypoint.sh

# Make sure the entrypoint script is executable
RUN chmod +x /app/entrypoint.sh

# Run the cron job and the app
CMD ["/app/entrypoint.sh"]