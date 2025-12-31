print("Initializing App Database...")
# This triggers the scan and creates the JSON file cleanly
from AppOpener import check
check.check_json()
print("SUCCESS: Database repaired.")