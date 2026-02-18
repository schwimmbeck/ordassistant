import base64
from types import SimpleNamespace

import pytest

pytest.importorskip("dotenv")
pytest.importorskip("ordec")

from contracts import ERR_PARSE_FAILURE, STAGE_PARSING
import validator


def test_validate_ord_code_full_maps_worker_error(monkeypatch):
    monkeypatch.setattr(
        validator,
        "_run_worker",
        lambda payload: {
            "ok": False,
            "error_stage": STAGE_PARSING,
            "error_code": ERR_PARSE_FAILURE,
            "error_message": "SyntaxError: bad input",
        },
    )

    result = validator.validate_ord_code_full("bad code")
    assert not result.success
    assert result.error_stage == STAGE_PARSING
    assert result.error_code == ERR_PARSE_FAILURE
    assert "SyntaxError" in result.error_message


def test_validate_ord_code_full_maps_worker_success(monkeypatch):
    svg_b64 = base64.b64encode(b"<svg/>").decode("ascii")
    monkeypatch.setattr(
        validator,
        "_run_worker",
        lambda payload: {
            "ok": True,
            "result": {
                "success": True,
                "svg_b64": svg_b64,
                "error_message": "",
                "error_stage": "",
                "error_code": "",
                "cell_names": ["Inv"],
                "spacing_violations": [],
            },
        },
    )

    result = validator.validate_ord_code_full("dummy")
    assert result.success
    assert result.svg_bytes == b"<svg/>"
    assert result.cell_names == ["Inv"]


def test_in_process_validation_assigns_parse_failure_code():
    result = validator._validate_ord_code_full_in_process("not valid ord")
    assert not result.success
    assert result.error_stage == STAGE_PARSING
    assert result.error_code == ERR_PARSE_FAILURE


class _BBox:
    def __init__(self, lx, ly, ux, uy):
        self.lx = lx
        self.ly = ly
        self.ux = ux
        self.uy = uy


class _Transform:
    def __init__(self, bbox):
        self._bbox = bbox

    def __mul__(self, _outline):
        return self._bbox


class _FakeInstance:
    def __init__(self, name, bbox):
        self._name = name
        self._bbox = bbox
        self.symbol = SimpleNamespace(outline=bbox)

    def full_path_str(self):
        return self._name

    def loc_transform(self):
        return _Transform(self._bbox)


class _FakePort:
    def __init__(self, name, x, y):
        self._name = name
        self.pos = SimpleNamespace(x=x, y=y)

    def full_path_str(self):
        return self._name


class _FakeView:
    def __init__(self, instances, ports):
        self.instances = instances
        self.ports = ports

    def all(self, cls):
        if cls.__name__ == "SchemInstance":
            return self.instances
        if cls.__name__ == "SchemPort":
            return self.ports
        return []


def test_spacing_checker_ignores_port_port_but_flags_port_instance_gap():
    inst = _FakeInstance("core", _BBox(0, 0, 5, 5))
    p1 = _FakePort("p1", 6, 2)  # 1-unit gap to instance -> violation
    p2 = _FakePort("p2", 7, 2)  # adjacent to p1, which is allowed
    view = _FakeView([inst], [p1, p2])

    violations = validator.check_layout_spacing(view, min_gap=2)
    assert violations
    assert any("core" in v for v in violations)
    assert not any("p1 and p2" in v for v in violations)
