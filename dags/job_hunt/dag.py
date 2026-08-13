import pendulum

from airflow.sdk import DAG, task
from airflow.providers.common.ai.toolsets.mcp import MCPToolset

from common.criteria import load_criteria
from job_hunt.types import JobVerdict
from job_hunt.tasks import email_digest, fetch_hn_jobs

# Connection of type "Pydantic AI" (conn_type: pydanticai) holding the
# OpenRouter API key in the password/API Key field.
LLM_CONN_ID = "openrouter_default"
PLAYWRIGHT_CONN_ID = "playwright_mcp"
MODEL_ID = "openrouter:z-ai/glm-5.2"


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
    "tags": ["daily", "jobs"],
}


SYSTEM_PROMPT = """\
You research job listings on behalf of a candidate and decide whether each one \
is worth applying to.

Use the Playwright tools to open the listing URL and then the company's own \
website (about, careers, team, product pages) to understand what the company \
does, its stage, and what the role actually involves. Do not search the wider \
web; the listing and the company site are enough.

The candidate's criteria are below. They define the verdict values, the hard-reject \
triggers, and the fields to extract; follow them directly.

--- CANDIDATE CRITERIA ---
{criteria}
--- END CANDIDATE CRITERIA ---

Return a verdict, one to three reasons citing specific JD language, any hard-reject \
triggers hit, and the extracted listing details. If the pages do not load or you cannot \
establish enough to judge the role, return 'review' rather than 'reject', and say so \
in the reasons.\
"""


with DAG("job_research", **DAG_ARGS) as dag:

    @task.agent(
        llm_conn_id=LLM_CONN_ID,
        model_id=MODEL_ID,
        system_prompt=SYSTEM_PROMPT.format(criteria=load_criteria()),
        output_type=JobVerdict,
        serialize_output=True,
        toolsets=[
            MCPToolset(
                mcp_conn_id=PLAYWRIGHT_CONN_ID,
                tool_prefix="playwright--",
            )
        ],
    )
    def research_job(job: dict) -> str:
        """Return the prompt; the agent's verdict becomes this task's XCom."""
        return (
            f"Research this job listing and decide whether to apply.\n\n"
            f"Title: {job['title']}\n"
            f"URL: {job['url']}\n"
            f"Details: {job['body'] or 'none provided'}"
        )

    @task
    def collect_approved(jobs: list[dict], verdicts: list[dict]) -> list[dict]:
        """Pair each listing with its verdict, dropping the rejects."""
        return [
            {**job, **verdict}
            for job, verdict in zip(jobs, verdicts)
            # if verdict["verdict"] != Verdict.REJECT
        ]

    listings = fetch_hn_jobs()
    verdicts = research_job.expand(job=listings)
    email_digest(collect_approved(listings, verdicts))  # type: ignore
