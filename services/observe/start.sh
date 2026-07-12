#!/bin/bash
cd "$(dirname "$0")"
# uvicorn entry point
python3 -m uvicorn src.app:app --host 0.0.0.0 --port 8002
