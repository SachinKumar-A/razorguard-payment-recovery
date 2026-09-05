# RazorGuard - control plane service.
#
# The benchmarks, the console and the service all run from this image; see
# DEPLOY.md for the commands.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    RAZORGUARD_STATE=/data/razorguard.db

WORKDIR /app

# Dependencies first, so a source change does not re-resolve the whole tree.
COPY requirements.txt requirements-service.txt ./
RUN pip install --no-cache-dir -r requirements-service.txt

COPY razorguard/ ./razorguard/
COPY app.py README.md ./
# Streamlit paints its own chrome before any of our CSS lands, so the base
# theme has to ship with the image or the container's first paint flashes dark
# and the widgets never match the page at all.
COPY .streamlit/config.toml ./.streamlit/
# The console renders the mark and wordmark; without these it falls back to
# plain text and the container quietly looks worse than the local run.
COPY assets/ ./assets/
COPY bench/results/ ./bench/results/

# Warm the console's cache while the image is being built. The run is
# deterministic, so a container that recomputes it on every start is spending
# three-quarters of a minute to arrive at a file it could have shipped with.
# Trades a slower build for a console that opens in about a second.
RUN python -c "from razorguard.console_data import run; run(2, 7, False); run(2, 7, True)"

# Unprivileged, and /data is a mount point: the state database must outlive the
# container or persistence buys nothing.
RUN useradd --create-home --uid 10001 razorguard \
 && mkdir -p /data \
 && chown -R razorguard:razorguard /app /data
USER razorguard
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

# One worker, deliberately. All control-plane state is in this process; two
# workers would each see half the ingest stream and disagree about the fleet.
CMD ["uvicorn", "razorguard.service:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
