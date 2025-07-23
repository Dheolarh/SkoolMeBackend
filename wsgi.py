import os
import shutil
import sys

# Add the backend directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Copy Google Cloud credentials to backend directory
source_creds = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skoolme-ocr-b933da63cd81.json")
target_creds = os.path.join(os.path.dirname(__file__), "skoolme-ocr-b933da63cd81.json")

if os.path.exists(source_creds) and not os.path.exists(target_creds):
    shutil.copy2(source_creds, target_creds)
    print("Copied Google Cloud credentials to backend directory")

from app import app

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
