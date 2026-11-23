FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 API_BASE_URL=http://backend:8000
WORKDIR /app
COPY frontend/requirements.txt /app/frontend/requirements.txt
COPY requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir -r /app/frontend/requirements.txt -c /app/requirements.lock.txt
COPY frontend /app/frontend
COPY .streamlit /app/.streamlit
RUN useradd --create-home --uid 10001 dashboard
USER dashboard
EXPOSE 8501
CMD ["streamlit", "run", "frontend/app.py", "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true", "--browser.gatherUsageStats", "false"]
