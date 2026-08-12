import pendulum
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta

from airflow.sdk import DAG, task
from airflow.providers.common.ai.toolsets.mcp import MCPToolset

from common.email import send_email

# Connection of type "Pydantic AI" (conn_type: pydanticai) holding the
# OpenRouter API key in the password/API Key field.
LLM_CONN_ID = "openrouter_default"
PLAYWRIGHT_CONN_ID = "playwright_mcp"
MODEL_ID = "openrouter:anthropic/claude-sonnet-4.5"

HACKERNEWS_BASE_URL = "https://hacker-news.firebaseio.com/v0"

DAG_ARGS = {
    "default_args": {
        "owner": "andrew",
        "retries": 0,
        "email_on_failure": True,
        "email_on_retry": False,
    },
    "schedule": "0 5 * * *",
    "start_date": pendulum.datetime(2026, 8, 7, tz="UTC"),
    "catchup": False,
    "max_active_runs": 1,
    "tags": ["daily"],
}


@dataclass
class HackerNewsJob:
    id: int
    time: int
    title: str
    text: str | None
    by: str
    score: int
    type: str
    url: str


with DAG("test_email", **DAG_ARGS) as dag:

    @task
    def fetch_daily_hackernews_jobs() -> list[HackerNewsJob]:
        """Fetch daily job listings from HackerNews"""
        r = requests.get(HACKERNEWS_BASE_URL + "/jobstories.json")
        jobs = []
        for job_id in r.json():
            r_job = requests.get(HACKERNEWS_BASE_URL + f"/item/{job_id}.json")
            job = HackerNewsJob(**r_job.json())
            ts = datetime.fromtimestamp(job.time)
            if datetime.now() - ts > timedelta(days=10):
                break
            jobs.append(job)
        return jobs

    

    @task.agent(
        llm_conn_id=LLM_CONN_ID,
        model_id=MODEL_ID,
        system_prompt=(
            "You write short internal status emails. Reply with a single "
            "paragraph of valid HTML and no surrounding markdown fence."
        ),
        toolsets=[
            MCPToolset(
                mcp_conn_id=PLAYWRIGHT_CONN_ID,
                tool_prefix="playwright--",
            )
        ],
    )
    def write_greeting() -> str:
        """Return the prompt; the agent's reply becomes this task's XCom."""
        return ""

    @task
    def send_test_email(content: str) -> str:
        return send_email(
            to="andrew.vagliano1@gmail.com",
            subject="Airflow test email",
            html=content,
        )

    send_test_email(write_greeting())  # type: ignore
