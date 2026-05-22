FROM python:3.12-slim

WORKDIR /app
COPY app.py /app/app.py

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SITE_ID=site
ENV SITE_NAME=Site
ENV SITE_KIND=site
ENV SITE_LANE=local
ENV CONTENT_ROOT=/app/runtime/websites

EXPOSE 8080

CMD ["python", "/app/app.py"]
