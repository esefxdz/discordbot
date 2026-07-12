"""Firebase project config — shared by all website integrations."""
######################################################################

# Your Firebase project (same one the website uses in comments.js)
PROJECT_ID = "esef-514bf"
API_KEY = "AIzaSyDuSjEGEKx5FnWYnQq8f_owbpYRBRrl5x0"

# Firestore REST API base URL
FIRESTORE_BASE = (
    f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}"
    f"/databases/(default)/documents"
)

# Document paths
DOC_SYSINFO = f"{FIRESTORE_BASE}/sysinfo/server"

# How often system stats are pushed (seconds)
SYSINFO_INTERVAL = 10
