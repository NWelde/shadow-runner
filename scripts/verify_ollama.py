"""Confirm the local Ollama instance and gemma4:4b are reachable.

Sends a hello-world prompt and prints the reply. Run this before the main CLI
to confirm setup is working:

    uv run python scripts/verify_ollama.py
"""

import ollama

response = ollama.chat(
    model="gemma3:4b",
    messages=[{"role": "user", "content": "Reply with: ollama is working"}],
)
print(response["message"]["content"])
