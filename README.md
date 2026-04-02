# Agents Audio

Streamlit browser for recorded `interview-agent` and `job-agent` sessions from Intervoo.

## Environment

Set these before running the app:

```bash
export DATABASE_URL="postgresql://user:password@host:5432/dbname"
export AGENT_TYPES="interview-agent,job-agent"
export AWS_S3_BUCKET="fl-ekstep"
export AWS_DEFAULT_REGION="ap-south-1"
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
```

`AGENT_TYPES` is optional and defaults to `interview-agent,job-agent`.
If the audio and transcript objects are private, the app will generate presigned S3 URLs from the AWS settings above.

## Run locally

```bash
uv run streamlit run app.py
```
