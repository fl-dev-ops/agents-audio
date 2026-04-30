from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from psycopg import connect
from psycopg.rows import dict_row

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "ap-south-1")
AWS_S3_ENDPOINT = os.getenv("AWS_S3_ENDPOINT", "")

print('DATABASE_URL', DATABASE_URL)

st.set_page_config(
    page_title="Intervoo Agent Sessions",
    page_icon="🎧",
    layout="wide",
)


def format_duration(duration_ms: int | float | None) -> str:
    if duration_ms is None or pd.isna(duration_ms) or duration_ms <= 0:
        return "—"
    total_seconds = int(duration_ms / 1000)
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {seconds:02d}s"


def format_datetime(value: Any) -> str:
    if value in (None, "") or pd.isna(value):
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %I:%M %p")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %I:%M %p")
    except ValueError:
        return str(value)


def normalize_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalize_optional_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def build_session_label(row: pd.Series) -> str:
    started = format_datetime(row.get("started_at"))
    phone_number = row.get("phone_number") or "unknown"
    return f"{started} | {row.get('agent_type', 'agent')} | {phone_number} | {row.get('id', '')}"


def parse_s3_location(
    url: str, fallback_bucket: str, s3_key: str
) -> tuple[str, str] | None:
    url = normalize_optional_text(url)
    s3_key = normalize_optional_text(s3_key)

    if fallback_bucket and s3_key:
        return fallback_bucket, s3_key

    if not url:
        return None

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None

    host_parts = parsed.netloc.split(".")
    path = parsed.path.lstrip("/")

    if host_parts and host_parts[0] == "s3":
        segments = path.split("/", 1)
        if len(segments) == 2:
            return segments[0], segments[1]

    if len(host_parts) >= 4 and host_parts[1] == "s3":
        return host_parts[0], path

    return None


def build_public_s3_url(bucket: str, key: str) -> str:
    if AWS_S3_ENDPOINT:
        return f"{AWS_S3_ENDPOINT.rstrip('/')}/{bucket}/{key}"
    return f"https://{bucket}.s3.{AWS_DEFAULT_REGION}.amazonaws.com/{key}"


def resolve_public_object_url(url: str, s3_key: str) -> str:
    url = normalize_optional_text(url)
    s3_key = normalize_optional_text(s3_key)

    if url:
        return url

    location = parse_s3_location(url, AWS_S3_BUCKET, s3_key)
    if location is None:
        return url

    bucket, key = location
    return build_public_s3_url(bucket, key)


@st.cache_data(ttl=300)
def load_sessions() -> tuple[pd.DataFrame | None, str | None]:
    if not DATABASE_URL:
        return None, "DATABASE_URL is not set."

    query = """
        SELECT
            id,
            agent_type,
            agent_name,
            livekit_room_name,
            livekit_room_sid,
            egress_id,
            resolved_user_id,
            participant_identity,
            phone_number,
            started_at,
            ended_at,
            duration_ms,
            status,
            egress_status,
            egress_error,
            audio_url,
            audio_s3_key,
            transcript_url,
            transcript_s3_key,
            metadata,
            created_at,
            updated_at
        FROM agent_sessions
        WHERE livekit_room_name LIKE %s
           OR livekit_room_name LIKE %s
        ORDER BY COALESCE(started_at, created_at) DESC
    """

    try:
        with connect(DATABASE_URL, row_factory=dict_row) as conn:
            rows = conn.execute(query, ("web_%", "call_%")).fetchall()
    except Exception as exc:
        return None, str(exc)

    if not rows:
        return pd.DataFrame(), None

    df = pd.DataFrame(rows)
    df["metadata"] = df["metadata"].apply(normalize_metadata)
    for column in [
        "audio_url",
        "audio_s3_key",
        "transcript_url",
        "transcript_s3_key",
        "resolved_user_id",
        "participant_identity",
        "phone_number",
        "livekit_room_name",
        "agent_name",
        "status",
        "egress_status",
    ]:
        df[column] = df[column].apply(normalize_optional_text)
    df["session_mode"] = df["metadata"].apply(
        lambda value: (
            value.get("session_mode")
            or value.get("sessionMode")
            or value.get("mode")
            or "practice"
        )
    )
    df["display_started_at"] = df["started_at"].apply(format_datetime)
    df["display_ended_at"] = df["ended_at"].apply(format_datetime)
    df["duration_fmt"] = df["duration_ms"].apply(format_duration)
    df["has_audio"] = df["audio_url"].fillna("").ne("")
    df["has_transcript"] = df["transcript_url"].fillna("").ne("")
    df["audio_stream_url"] = df.apply(
        lambda row: resolve_public_object_url(
            row.get("audio_url") or "",
            row.get("audio_s3_key") or "",
        ),
        axis=1,
    )
    df["transcript_stream_url"] = df.apply(
        lambda row: resolve_public_object_url(
            row.get("transcript_url") or "",
            row.get("transcript_s3_key") or "",
        ),
        axis=1,
    )
    return df, None


@st.cache_data(ttl=3600)
def load_transcript(url: str) -> tuple[dict[str, Any] | None, str | None]:
    if not url:
        return None, "No transcript URL available."

    try:
        with urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return None, str(exc)

    if not isinstance(payload, dict):
        return None, "Transcript payload is not a JSON object."
    return payload, None


st.title("🎧 Intervoo Agent Sessions")
st.markdown("Browse, filter, and listen to recorded sessions for the Intervoo agents.")

df, error = load_sessions()

if error:
    st.error(f"Database connection error: {error}")
    st.info("Set `DATABASE_URL` in the app environment before running Streamlit.")
    st.stop()

if df is None or df.empty:
    st.warning("No sessions found.")
    st.stop()

st.sidebar.header("Filters")
filtered_df = df.copy()
search_query = st.sidebar.text_input("Search", "")
if search_query:
    mask = (
        filtered_df["phone_number"].fillna("").str.contains(search_query, case=False)
        | filtered_df["resolved_user_id"]
        .fillna("")
        .str.contains(search_query, case=False)
        | filtered_df["participant_identity"]
        .fillna("")
        .str.contains(search_query, case=False)
        | filtered_df["livekit_room_name"]
        .fillna("")
        .str.contains(search_query, case=False)
    )
    filtered_df = filtered_df[mask]

mode_options = {
    "All": None,
    "Phone": "practice",
    "Web": "diagnostics",
}
selected_mode_label = st.sidebar.selectbox("Mode", list(mode_options.keys()))
selected_mode = mode_options[selected_mode_label]
if selected_mode is not None:
    filtered_df = filtered_df[filtered_df["session_mode"] == selected_mode]

st.sidebar.markdown("---")
st.sidebar.markdown(f"**Total Sessions:** {len(df)}")
st.sidebar.markdown(f"**Filtered Sessions:** {len(filtered_df)}")

st.markdown("---")

total_duration_ms = filtered_df["duration_ms"].fillna(0).sum()
audio_count = int(filtered_df["has_audio"].sum())
transcript_count = int(filtered_df["has_transcript"].sum())
unique_users = int(
    filtered_df["resolved_user_id"]
    .replace("", pd.NA)
    .fillna(filtered_df["phone_number"].replace("", pd.NA))
    .dropna()
    .nunique()
)
total_sessions = len(filtered_df)
interview_sessions = int((filtered_df["agent_type"] == "interview-agent").sum())
job_sessions = int((filtered_df["agent_type"] == "job-agent").sum())

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Sessions", f"{total_sessions:,}")
with col2:
    st.metric("Interview Agent Sessions", f"{interview_sessions:,}")
with col3:
    st.metric("Job Agent Sessions", f"{job_sessions:,}")

st.markdown("---")

if filtered_df.empty:
    st.warning("No sessions match the selected filters.")
    st.stop()

display_df = filtered_df.copy()
display_df["audio_link"] = display_df["audio_stream_url"].fillna("")
display_df["transcript_link"] = display_df["transcript_stream_url"].fillna("")
display_df["user"] = display_df["resolved_user_id"].fillna("")
display_df["phone"] = display_df["phone_number"].fillna("")
display_df["room"] = display_df["livekit_room_name"].fillna("")

table_cols = [
    "display_started_at",
    "agent_type",
    "session_mode",
    "status",
    "user",
    "phone",
    "room",
    "duration_fmt",
    "audio_link",
    "transcript_link",
]

table_names = {
    "display_started_at": "Started",
    "agent_type": "Agent",
    "session_mode": "Mode",
    "status": "Status",
    "user": "User",
    "phone": "Phone",
    "room": "Room",
    "duration_fmt": "Duration",
    "audio_link": "Audio",
    "transcript_link": "Transcript",
}

st.dataframe(
    display_df[table_cols].rename(columns=table_names),
    column_config={
        "Audio": st.column_config.LinkColumn("Audio", display_text="🔊 Listen"),
        "Transcript": st.column_config.LinkColumn("Transcript", display_text="📝 Open"),
    },
    hide_index=True,
    use_container_width=True,
)

st.markdown("---")
st.subheader("📥 Bulk Download")

col1, col2, col3 = st.columns(3)
with col1:
    export_columns = [
        "id",
        "agent_type",
        "agent_name",
        "resolved_user_id",
        "participant_identity",
        "phone_number",
        "livekit_room_name",
        "started_at",
        "ended_at",
        "duration_ms",
        "status",
        "audio_url",
        "transcript_url",
        "egress_status",
    ]
    st.download_button(
        label="📊 Download CSV",
        data=filtered_df[export_columns].to_csv(index=False),
        file_name="intervoo_agent_sessions.csv",
        mime="text/csv",
    )
with col2:
    audio_urls = [
        url for url in filtered_df["audio_stream_url"].fillna("").tolist() if url
    ]
    if audio_urls:
        st.download_button(
            label="🔊 Download Audio URL List",
            data="\n".join(audio_urls),
            file_name="session_audio_urls.txt",
            mime="text/plain",
        )
    else:
        st.info("No audio URLs to export.")
with col3:
    transcript_urls = [
        url for url in filtered_df["transcript_stream_url"].fillna("").tolist() if url
    ]
    if transcript_urls:
        st.download_button(
            label="📝 Download Transcript URL List",
            data="\n".join(transcript_urls),
            file_name="session_transcript_urls.txt",
            mime="text/plain",
        )
    else:
        st.info("No transcript URLs to export.")

st.markdown("---")
st.caption("Data is cached for 5 minutes.")
