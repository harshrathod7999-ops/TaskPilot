# TaskPilot backend — self-contained image (deploys to Render / HF Spaces, free tier).
#
# Build from THIS backend directory (everything it needs is vendored here):
#   docker build -t taskpilot-api .
#
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + the custom MCP server + the vendored shared router/tracing.
COPY app ./app
COPY mcp_server ./mcp_server
COPY shared ./shared

EXPOSE 8001
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8001"]
