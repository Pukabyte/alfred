FROM python:3.9-slim

# Install required packages
RUN apt-get update && apt-get install -y \
    inotify-tools \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script
COPY alfred.py .

# Create volume for database persistence
VOLUME ["/app/data"]

# Run the script
CMD ["python", "alfred.py", "--no-confirm"] 