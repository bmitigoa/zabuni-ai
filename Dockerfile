# Zabuni AI, hosted demo image (Hugging Face Spaces, Docker SDK) and a reproducible local build.
#   docker build -t zabuni-ai .  &&  docker run -p 7860:7860 -e GROQ_API_KEY=... zabuni-ai
#
# Python 3.12 runs the agent, the MCP server and the Streamlit app; Node 20 runs the official Filesystem MCP server (npx).
# The frozen OCDS snapshot is NOT in the repository: the build downloads it from a private HF dataset with the HF_TOKEN secret,
# checks its SHA-256 against data/snapshot.sha256, and loads it into DuckDB AT BUILD TIME, so the container starts in seconds
# and never downloads newer data. The Groq key is NOT in the image: it arrives as the GROQ_API_KEY secret at run time.

FROM node:20-slim AS node

FROM python:3.12-slim
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Spaces run the container as uid 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetch the pinned Filesystem MCP server so the first run does not wait for npm (same version as agent/settings.py)
RUN npm install -g --prefix /home/user/.local @modelcontextprotocol/server-filesystem@2026.8.31

COPY --chown=user . .

# Build the analysis database from the frozen snapshot, then drop the raw file from the image
RUN --mount=type=secret,id=HF_TOKEN,mode=0444,required=true \
    python deploy/fetch_snapshot.py && python data/loader.py && rm -f data/raw/full.jsonl.gz

# Non-secret settings; GROQ_API_KEY and DEMO_VIDEO_URL are set as Space secret / variable
ENV LLM_PROVIDER=groq MODEL=qwen/qwen3.8-27b
EXPOSE 7860
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true", \
     "--server.enableCORS=false", "--server.enableXsrfProtection=false", "--browser.gatherUsageStats=false"]
