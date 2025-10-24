import os

# Paths and keys
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS", "firebase-service-account.json")
GCP_PROJECT = os.getenv("GCP_PROJECT", "your-gcp-project-id")
LOCATION = "us-central1"  # or region where Vertex AI is deployed
MODEL_NAME = "gemini-1.5-flash"  # Lightweight Gemini model

# Optionally store API keys
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = FIREBASE_CREDENTIALS
