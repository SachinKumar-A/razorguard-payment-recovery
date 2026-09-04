# RevenueGuard - control plane service.
#
# Builds an image that runs the HTTP service. The benchmarks and the operator
# console run from the same image; see DEPLOY.md for the commands.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a source change does not re-resolve the whole tree.
COPY requirements.txt requirements-service.txt ./
RUN pip install --no-cache-dir -r requirements-service.txt

COPY revenueguard/ ./revenueguard/
COPY app.py README.md ./
COPY bench/results/ ./bench/results/

# Run unprivileged. Nothing here needs root, and the service holds payment
# aggregates even if it never holds individual records.
RUN useradd --create-home --uid 10001 revenueguard \
 && chown -R revenueguard:revenueguard /app
USER revenueguard

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "revenueguard.service:app", "--host", "0.0.0.0", "--port", "8000"]
