import os
import boto3
import logging
import sys
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO)

R2_ENDPOINT = os.environ.get('R2_ENDPOINT')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET = os.environ.get('R2_BUCKET')
LANCE_DIR = "/app/storage"

if not R2_BUCKET:
    logging.error("R2_BUCKET must be set")
    sys.exit(1)

# Endpoint and keys are optional. When unset (prod on EC2), boto3
# uses the regional default endpoint and the default credential
# chain (instance profile via IMDS).
s3_kwargs = {}
if R2_ENDPOINT:
    s3_kwargs['endpoint_url'] = R2_ENDPOINT
if R2_ACCESS_KEY_ID:
    s3_kwargs['aws_access_key_id'] = R2_ACCESS_KEY_ID
if R2_SECRET_ACCESS_KEY:
    s3_kwargs['aws_secret_access_key'] = R2_SECRET_ACCESS_KEY

s3 = boto3.client("s3", **s3_kwargs)

def should_upload(local_path, s3_key):
    try:
        response = s3.head_object(Bucket=R2_BUCKET, Key=s3_key)
        return os.path.getsize(local_path) != response['ContentLength']
    except ClientError as e:
        if e.response['Error']['Code'] == "404":
            return True
        logging.warning(f"Could not check {s3_key}: {e}")
        return True

def sync_folder():
    if not os.path.exists(LANCE_DIR):
        logging.info(f"Lance directory {LANCE_DIR} does not exist yet")
        return

    try:
        for root, _, files in os.walk(LANCE_DIR):
            for file in files:
                local_path = os.path.join(root, file)
                rel_path = os.path.relpath(local_path, LANCE_DIR)
                s3_key = f"lance/{rel_path}"

                if should_upload(local_path, s3_key):
                    logging.info(f"Uploading {s3_key}")
                    extra_args = {}
                    # Tag only image binaries so the 30-day
                    # lifecycle rule expires user uploads. The
                    # existing seed set lacks this tag and is kept.
                    # Lance data files are never tagged.
                    if s3_key.startswith("lance/images/"):
                        extra_args["Tagging"] = "retention=auto-expire"
                    s3.upload_file(local_path, R2_BUCKET, s3_key, ExtraArgs=extra_args)

        logging.info("Sync completed successfully")
    except Exception as e:
        logging.error(f"Sync failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    sync_folder()
