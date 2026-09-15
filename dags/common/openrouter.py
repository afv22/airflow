from airflow.sdk import BaseHook
from pydantic_ai.providers.openrouter import OpenRouterProvider

LLM_CONN_ID = "openrouter_default"


def provider():
    conn = BaseHook.get_connection(LLM_CONN_ID)
    return OpenRouterProvider(api_key=conn.password)
