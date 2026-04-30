# Agents Audio

Streamlit browser for recorded Intervoo agent sessions.

## Environment

Set these before running the app:

```bash
export DATABASE_URL="postgresql://user:password@host:5432/dbname"
export AWS_S3_BUCKET="fl-ekstep"
export AWS_DEFAULT_REGION="ap-south-1"
```

`AWS_S3_BUCKET` is only needed when the database stores `audio_s3_key` or `transcript_s3_key` without a full public URL.
`AWS_S3_ENDPOINT` is optional for S3-compatible storage or custom public object hosts.

## Run locally

```bash
uv run streamlit run app.py
```
