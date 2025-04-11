FROM --platform=$TARGETPLATFORM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application files
COPY alfred.py .
COPY web/ web/

# Create volume for database persistence
VOLUME ["/app/data"]

# Expose port for web interface
EXPOSE 5000

# Create startup script
RUN echo '#!/bin/bash\n\
# Start alfred.py in the background\n\
python alfred.py --no-confirm &\n\
# Wait a moment for database initialization\n\
sleep 2\n\
# Start web interface\n\
python web/app.py\n\
# If web interface exits, kill alfred.py\n\
kill %1' > /app/start.sh && \
chmod +x /app/start.sh

# Command to run both the symlink manager and web interface
CMD ["/app/start.sh"] 