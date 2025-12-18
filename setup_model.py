import os
import zipfile
import urllib.request
import shutil

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_DIR = "models"
MODEL_NAME = "vosk-model-small-en-us-0.15"

def setup_voice_model():
    # 1. Create models directory
    if not os.path.exists(MODEL_DIR):
        os.makedirs(MODEL_DIR)
        print(f"Created directory: {MODEL_DIR}")

    # 2. Check if model already exists
    final_path = os.path.join(MODEL_DIR, MODEL_NAME)
    if os.path.exists(final_path):
        print(f"Model found at {final_path}. Setup complete.")
        return

    # 3. Download
    print("Downloading model (approx 40MB)... this may take a moment.")
    zip_path = "model.zip"
    urllib.request.urlretrieve(MODEL_URL, zip_path)
    print("Download complete.")

    # 4. Extract
    print("Extracting...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(MODEL_DIR)
    
    # 5. Cleanup
    os.remove(zip_path)
    print(f"Success! Model installed to: {final_path}")

if __name__ == "__main__":
    setup_voice_model()