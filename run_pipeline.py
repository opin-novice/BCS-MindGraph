"""Entry point — run with: python run_pipeline.py [args]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from bcs.pipeline.main_pipeline import main

if __name__ == "__main__":
    sys.argv[0] = "run_pipeline.py"
    main()
