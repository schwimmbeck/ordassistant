"""Subprocess worker for isolated ORD validation."""

from __future__ import annotations

import json
import traceback

from contracts import ERR_VALIDATION_RUNTIME, STAGE_RUNTIME
from validator import (
    _fix_spacing_in_process,
    _result_to_payload,
    _validate_ord_code_full_in_process,
)


def main() -> int:
    try:
        raw = input()
        payload = json.loads(raw)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_stage": STAGE_RUNTIME,
                    "error_code": ERR_VALIDATION_RUNTIME,
                    "error_message": f"Failed to read worker input: {exc}",
                }
            )
        )
        return 0

    operation = payload.get("operation", "validate")
    source = payload.get("source", "")
    test_params = payload.get("test_params")

    try:
        if operation == "fix_spacing":
            changes = payload.get("changes", [])
            result = _fix_spacing_in_process(source, changes, test_params=test_params)
        else:
            result = _validate_ord_code_full_in_process(source, test_params=test_params)
        print(json.dumps({"ok": True, "result": _result_to_payload(result)}))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_stage": STAGE_RUNTIME,
                    "error_code": ERR_VALIDATION_RUNTIME,
                    "error_message": "".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    ),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
