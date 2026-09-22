"""Make `examples/` importable from the tests without installing it.

`examples` is deliberately not in the wheel (`pyproject.toml`), so the gate runs
from a checkout: this puts the project root on the path the way an editable
install of the package itself already is.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
