"""把 repo 根目錄加入 sys.path，讓 tests/ 可以 `import models` / `import collectors`。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
