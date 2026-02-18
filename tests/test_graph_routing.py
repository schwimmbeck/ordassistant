from config import MAX_CIRCUIT_RETRIES
from contracts import ERR_SPACING_VIOLATION
from graph import route_after_circuit_validation


def test_route_after_validation_success_goes_to_format():
    route = route_after_circuit_validation({"circuit_validation_success": True})
    assert route == "format_response"


def test_route_spacing_error_uses_layout_retry():
    route = route_after_circuit_validation(
        {
            "circuit_validation_success": False,
            "circuit_error_code": ERR_SPACING_VIOLATION,
            "spacing_attempt": 0,
        }
    )
    assert route == "increment_spacing_attempt"


def test_route_spacing_error_stops_after_retry_limit():
    route = route_after_circuit_validation(
        {
            "circuit_validation_success": False,
            "circuit_error_code": ERR_SPACING_VIOLATION,
            "spacing_attempt": 999,
        }
    )
    assert route == "format_response"


def test_route_non_spacing_retries_until_final_allowed_attempt():
    route = route_after_circuit_validation(
        {
            "circuit_validation_success": False,
            "circuit_error_code": "parse_failure",
            "circuit_attempt": 0,
        }
    )
    assert route == "increment_circuit_attempt"

    route = route_after_circuit_validation(
        {
            "circuit_validation_success": False,
            "circuit_error_code": "parse_failure",
            "circuit_attempt": max(MAX_CIRCUIT_RETRIES - 1, 0),
        }
    )
    assert route == "format_response"
