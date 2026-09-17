"""Launch with python -m research; optional --debug enables application debug logs."""

import argparse
import logging
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="CreatorRadar Research Console")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    (root / "data").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(root / "data" / "research.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    # HTTP libraries may log URLs (containing API keys) at DEBUG; keep them quiet.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    try:
        from PySide6.QtWidgets import QApplication

        from research.gui import ResearchConsole
    except ImportError:
        raise SystemExit("GUI dependencies missing. Run: python -m pip install -r requirements.txt") from None
    app = QApplication(sys.argv[:1])
    window = ResearchConsole(root)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
