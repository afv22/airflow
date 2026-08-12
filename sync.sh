cd /home/andrew/data/airflow
curl -sSL https://raw.githubusercontent.com/apache/airflow/constraints-3.3.0/constraints-3.13.txt \
| grep -v '^apache-airflow-providers-common-ai==' > /tmp/constraints-filtered.txt
uv pip compile requirements-dev.txt -c /tmp/constraints-filtered.txt -o requirements-dev.lock
uv pip sync requirements-dev.lock