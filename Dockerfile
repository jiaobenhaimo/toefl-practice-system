FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create tests directory (will be mounted as volume in production)
RUN mkdir -p tests

# Expose port
EXPOSE 8080

# Run the application
CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8080"]