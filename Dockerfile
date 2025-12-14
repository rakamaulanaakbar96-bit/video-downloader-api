FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for yt-dlp
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY main.py .

# Expose port
EXPOSE 7860

# Run the app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
