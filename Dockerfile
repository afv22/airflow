# Stock Airflow plus every dependency in uv.lock.
#
# The lock file is the single source of truth: add or remove packages with
# `uv add` / `uv remove` on a laptop, test locally, push. CI builds this image
# and pushes it to the Forgejo registry, and the deploy script on mainframe
# pulls whatever tag Ansible points the compose file at. Nothing here is
# edited by hand on the server.
#
# Only pyproject.toml and uv.lock enter the build context (see .dockerignore).
# DAGs are bind-mounted from the git checkout at runtime, not baked in, so a
# DAG-only push produces byte-identical layers, the registry digest does not
# move, and `docker compose pull` on the host is a no-op.
FROM apache/airflow:3.3.0

COPY --chown=airflow:0 pyproject.toml uv.lock /tmp/project/

# The image installs Airflow into /home/airflow/.local, which is where
# `python` on PATH resolves. uv is already shipped in the image. The lock
# pins apache-airflow==3.3.0 itself, so the export is a full, transitively
# pinned set: prod ends up with the exact versions the tests ran against.
#
# --no-emit-project: the lock also lists this repo as a package, and there is
# nothing to install for it -- the DAGs are mounted, not installed.
# --no-hashes: pip-style hash checking fails on packages uv resolved from a
# different index than the wheel it downloads here; the versions are pinned
# either way.
RUN cd /tmp/project \
    && uv export --frozen --no-dev --no-hashes --no-emit-project \
         --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --no-cache --python "$(command -v python)" \
         -r /tmp/requirements.txt \
    && rm -rf /tmp/project /tmp/requirements.txt
