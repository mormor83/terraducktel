"""Detect obvious secret material in Terraform state JSON before persistence."""
import json
import re
from typing import Any

# AWS access key id pattern (20 chars after AKIA)
_AWS_ACCESS_KEY_ID = re.compile(r"AKIA[0-9A-Z]{16}")
# PEM / generic high-entropy blocks (conservative)
_PEM_BLOCK = re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----")


def scan_terraform_state_json(data: Any) -> tuple[bool, str | None]:
    """Return (True, None) if clean; (False, reason) if suspicious content found."""
    try:
        text = json.dumps(data, default=str)
    except (TypeError, ValueError):
        return False, "State payload is not JSON-serializable"

    if _AWS_ACCESS_KEY_ID.search(text):
        return False, (
            "Terraform state appears to contain AWS access key patterns (secret material)"
        )
    if _PEM_BLOCK.search(text):
        return False, "Terraform state appears to contain private key material"
    return True, None
