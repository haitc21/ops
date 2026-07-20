"""CLI entry to write/refresh the contract checksum manifest."""

from __future__ import annotations

from ops.contracts.validate import write_contract_manifest


def main() -> None:
    result = write_contract_manifest()
    if not result.ok:
        raise SystemExit(f"failed to write contract manifest: {result.message}")
    print(f"manifest written ({result.file_count} files)")


if __name__ == "__main__":
    main()
