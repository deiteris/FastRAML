"""`python -m fastraml.cli`, the console script without the installed entry point."""

import sys

from fastraml.cli import main

sys.exit(main())
