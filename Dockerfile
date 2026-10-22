FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends gettext && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN SECRET_KEY=build python manage.py collectstatic --noinput -v0 \
    && (ls locale/*/LC_MESSAGES/*.po >/dev/null 2>&1 && python manage.py compilemessages -v0 || true)

RUN useradd --create-home ooda && mkdir -p /data && chown ooda /data
USER ooda
VOLUME /data
EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 3 --access-logfile -"]
