#!/bin/bash

echo "--- Starting custom build process ---"

# Install dependencies from requirements.txt
python3 -m pip install -r requirements.txt

# Install Flask with async extra
python3 -m pip install "Flask[async]"

echo "--- Custom build process finished ---"