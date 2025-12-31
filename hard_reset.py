import os
import sys

def nuke_corrupted_db():
    print("Searching for corrupted AppOpener database...")
    
    target_path = None
    
    # 1. Search all python paths for the library
    for path in sys.path:
        possible_dir = os.path.join(path, "AppOpener")
        if os.path.exists(possible_dir):
            target_path = os.path.join(possible_dir, "data.json")
            break
            
    if target_path and os.path.exists(target_path):
        print(f"Found corrupted file: {target_path}")
        try:
            # 2. Delete the file
            os.remove(target_path)
            print(">> SUCCESS: Deleted corrupted database.")
            
            # 3. Create a clean empty file to prevent "File Not Found" errors
            with open(target_path, 'w') as f:
                f.write("{}")
            print(">> SUCCESS: Created clean database template.")
            
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Could not find 'data.json'. Attempting to force-create one...")
        # Try to find the directory again to create the file
        for path in sys.path:
            possible_dir = os.path.join(path, "AppOpener")
            if os.path.exists(possible_dir):
                target_path = os.path.join(possible_dir, "data.json")
                with open(target_path, 'w') as f:
                    f.write("{}")
                print(f"Created fresh database at: {target_path}")
                break

if __name__ == "__main__":
    nuke_corrupted_db()
    print("\nNOW RUN: python main.py")