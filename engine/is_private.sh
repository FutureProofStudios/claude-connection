#!/bin/sh
# Exits 0 only if GitHub says this repo is private. Checks the API's
# `private` field, not the HTTP status: sandboxed sessions reach GitHub
# through an authenticated proxy, so a public and a private repo both
# answer 200 there. A logged-out request to a private repo gets "Not Found".
curl -s https://api.github.com/repos/FutureProofStudios/claude-connection | python3 -c '
import json, sys
d = json.load(sys.stdin)
private = d.get("private") is True or d.get("message") == "Not Found"
print("private" if private else "PUBLIC: do not commit data/ or reports/")
sys.exit(0 if private else 1)
'
