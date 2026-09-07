FROM python:3.12-slim

WORKDIR /app

# Deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY arr_client.py config.py mcp_instance.py server.py ./
COPY tools/ ./tools/

# Run as non-root
RUN useradd -m app && chown -R app /app
USER app

EXPOSE 8787
CMD ["python", "server.py"]
