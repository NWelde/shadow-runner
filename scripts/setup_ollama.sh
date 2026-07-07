#!/bin/bash
# Installs Ollama inside WSL2, pulls gemma4:4b, and verifies the GPU is in use.
# Run once before using the triage/pitch commands:
#     bash scripts/setup_ollama.sh
set -euo pipefail

echo "Installing Ollama ..."
curl -fsSL https://ollama.com/install.sh | sh

echo "Pulling gemma4:4b (this can take a few minutes) ..."
ollama pull gemma4:4b

echo "Verifying GPU usage — check that GPU-Util spikes in the output below:"
nvidia-smi
