FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir falkordb
COPY scripts/ /app/scripts/
CMD ["tail", "-f", "/dev/null"]
