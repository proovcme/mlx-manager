"""Compatibility entry point for the manager component."""
import sys
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parent/'manager'))
    from cli import main
    main()
