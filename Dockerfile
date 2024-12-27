# Use an official Python image
FROM python:3.10-slim

# Set environment variables to prevent Python from writing .pyc files and to enable logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Install system dependencies (e.g., ffmpeg, which Whisper requires) and clean up to reduce image size
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy only the requirements.txt first to leverage Docker cache
COPY requirements.txt /app/

# Install Python dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install OpenAI Whisper directly in the Dockerfile
RUN pip install openai-whisper

# Copy the application code
COPY . /app/

# Expose the port Flask runs on
EXPOSE 5000

# Run the Flask app
CMD ["python", "consumer.py"]