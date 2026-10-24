FROM python:3.12

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/

ARG UID=1000
ARG GID=1000
ARG PROJECT_USER=vcsinstall

RUN apt-get update \
    && apt-get install -y --no-install-recommends taskwarrior \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "$GID" "$PROJECT_USER" \
    && useradd --create-home --uid "$UID" --gid "$PROJECT_USER" --shell /bin/bash "$PROJECT_USER"

USER $PROJECT_USER

WORKDIR /repository

ENV UV_PROJECT_ENVIRONMENT=/home/vcsinstall/.venv
ENV UV_PYTHON_DOWNLOADS=never
ENV UV_LINK_MODE=copy
ENV VCSINSTALL_DEV_CONTAINER=1
ENV PATH="/home/vcsinstall/.venv/bin:${PATH}"

# Uncomment with the local uv source and Compose build context.
# COPY --from=llm-tool-cli --chown=$PROJECT_USER:$PROJECT_USER . /llm-tool-cli
COPY --chown=$PROJECT_USER:$PROJECT_USER . /repository

RUN uv sync --locked

ENV UV_NO_SYNC=1

CMD []
