"""Allow ``python -m investment_agent`` when the console script is not on PATH."""

from investment_agent.main import main

raise SystemExit(main())
