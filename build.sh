#!/bin/bash

# Update pip to the latest version first
python3 -m pip install --upgrade pip

# Install dependencies from requirements.txt
python3 -m pip install -r requirements.txt

# Install Flask with async extra
python3 -m pip install "Flask[async]"
