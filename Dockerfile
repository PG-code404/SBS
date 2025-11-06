FROM python:3-slim

# Set working directory
WORKDIR /SBS

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . .

# Create non-root user and set permissions
RUN adduser -u 5678 --disabled-password --gecos "" appuser \
    && chown -R appuser /SBS \
    && chmod -R 777 /SBS

# Switch to non-root user
USER appuser

# Expose port 8080 for Cloud Run
EXPOSE 8080

# Run Flask app with Waitress
CMD ["python", "run.py"]
