FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN useradd -r -u 10001 saathi && mkdir -p /var/lib/saathi/media && chown -R saathi /var/lib/saathi
USER saathi
EXPOSE 8000
CMD ["saathi-api"]
