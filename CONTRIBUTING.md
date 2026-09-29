# Contributing to Teams Exporter

Thank you for your interest in the project.

## Development environment

Requirements: Python 3.11 or newer and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv build
```

## Proposing a change

1. Open an issue first for substantial changes.
2. Create a short, focused branch.
3. Add or update tests alongside the code.
4. Run the tests, lint checks, and package build.
5. Describe user-visible effects, required Microsoft Graph permissions, and any data
   deletion risks in the pull request.

Never include real Teams messages, HTML exports, tenant identifiers, tokens, work email
addresses, or local configuration files in an issue, test, or commit. Use fictional data
only.

## Reporting a vulnerability

Follow the private process in [SECURITY.md](SECURITY.md). Do not publish exploitable
details in a public issue.
