# nova-control: NovaAgent control plane (+ the NovaAgent web build copied in by the build script)
FROM python:3.12-slim
ARG PIP_INDEX_URL
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} -r requirements.txt
COPY nova_control ./nova_control
COPY web /srv/nova-web
RUN useradd -u 10002 -m nova
USER 10002
EXPOSE 8080 9090
CMD ["uvicorn", "nova_control.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
