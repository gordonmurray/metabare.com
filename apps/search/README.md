# Deploy

```
fly app create metabase-search
```

Set secrets

```
fly secrets set \
  R2_ENDPOINT="https://xx.r2.cloudflarestorage.com" \
  R2_ACCESS_KEY_ID="xx" \
  R2_SECRET_ACCESS_KEY="xxx" \
  R2_BUCKET="metabare-com" \
  BASE_IMAGE_URL="https://metabare.com"
```

Cert
```
fly certs create sarch.metabare.com

# check/verify
fly certs check search.metabare.com
```

Deploy
```
fly deploy
```

Test
```
curl "https://search.metabare.com/search?text=forest"
```