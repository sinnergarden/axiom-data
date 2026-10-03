"""Teaching source moved to axiom-docs/examples; no duplicate implementation."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "axiom-docs" / "examples/render_notebook.py"), run_name="__main__")
