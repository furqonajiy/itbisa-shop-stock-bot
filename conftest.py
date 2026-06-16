import os
import sys

# Make the repo root importable so tests can do `from src... import ...`
# regardless of pytest's import mode / rootdir handling.
sys.path.insert(0, os.path.dirname(__file__))
