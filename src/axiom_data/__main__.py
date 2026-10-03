"""Allow ``python -m axiom_data`` to use the current CLI."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
