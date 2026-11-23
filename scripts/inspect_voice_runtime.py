import importlib.util
import importlib.metadata as metadata
import json
import sys

print(json.dumps({'interpreter': sys.executable, 'python': sys.version.split()[0],
    'available': {name: bool(importlib.util.find_spec(name)) for name in
                  ['faster_whisper', 'piper', 'av', 'numpy', 'huggingface_hub', 'onnxruntime']},
    'versions': {name: metadata.version(name) for name in ['openai', 'streamlit', 'fastapi', 'langgraph']}}))
