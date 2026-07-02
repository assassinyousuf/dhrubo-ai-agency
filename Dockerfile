# Use the official Microsoft Playwright image which includes all Chromium dependencies
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging files and install
COPY pyproject.toml ./
# Because we are using an editable install or modern packaging without setup.py,
# we need to copy the source code first to install it successfully.
COPY src/ ./src/
COPY config/ ./config/
COPY frontend/ ./frontend/

# Install the application and its dependencies
RUN pip install --upgrade pip && \
    pip install -e .[browser,pdf,api]

# Create directories for volumes
RUN mkdir -p /app/output /app/dataset

# Expose the FastAPI port
EXPOSE 8000

# Start the API server by default
CMD ["python", "-m", "uvicorn", "dhrubo.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
