import os
import json
from AppOpener import check

# Force reset the internal JSON file
print("Repairing AppOpener database...")
try:
    check.check_json()
    print("Database check complete.")
    
    # Reload to verify
    from AppOpener import open as app_open
    print("AppOpener loaded successfully.")
    print("FIX COMPLETE. You can now delete this file.")
except Exception as e:
    print(f"Error: {e}")
    # If it fails, we manually delete the file
    try:
        import AppOpener
        path = os.path.dirname(AppOpener.__file__)
        json_path = os.path.join(path, "data.json")
        if os.path.exists(json_path):
            os.remove(json_path)
            print(f"Deleted corrupted file: {json_path}")
            print("Please run this script again to rebuild it.")
    except:
        print("Could not find AppOpener path.")