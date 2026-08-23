# Reproduce the evaluation offline. No GPU, no API keys.
#   docker build -t viktor-router .
#   docker run --rm -v "$PWD/export:/app/export" viktor-router
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir tiktoken matplotlib
COPY . .
CMD ["./evaluation/run.sh", "export"]
