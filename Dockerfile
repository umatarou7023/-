FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py shifts.py daily_shifts.py ./
COPY static ./static
ENV SHIFT_HOST=0.0.0.0 SHIFT_DATA_DIR=/data PORT=8000
EXPOSE 8000
CMD ["python", "app.py"]
