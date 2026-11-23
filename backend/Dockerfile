FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
COPY requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt -c /app/requirements.lock.txt
COPY backend /app/backend
COPY scripts /app/scripts
COPY sample_data /app/sample_data
RUN useradd --create-home --uid 10001 hiring && mkdir -p /app/storage/resumes && chown -R hiring:hiring /app/storage
USER hiring
EXPOSE 8000
CMD ["sh", "-c", "python -m alembic -c backend/alembic.ini upgrade head && python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000"]
