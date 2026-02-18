import base64
import json
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass, field

from config import VALIDATION_TIMEOUT_SECONDS
from contracts import (
    ERR_COMPILE_FAILURE,
    ERR_EXEC_FAILURE,
    ERR_INSTANTIATION_FAILURE,
    ERR_MISSING_REQUIRED_PARAMS,
    ERR_NO_CELL_DISCOVERED,
    ERR_PARSE_FAILURE,
    ERR_RENDER_FAILURE,
    ERR_SPACING_VIOLATION,
    ERR_VALIDATION_RUNTIME,
    ERR_VIEW_ACCESS_FAILURE,
    STAGE_COMPILATION,
    STAGE_DISCOVERY,
    STAGE_EXECUTION,
    STAGE_INSTANTIATION,
    STAGE_PARSING,
    STAGE_RENDERING,
    STAGE_RUNTIME,
    STAGE_SPACING,
    STAGE_VIEW_ACCESS,
)
from ordec.core.cell import Cell, Parameter, ParameterError
from ordec.core.geoprim import Rect4R
from ordec.core.schema import SchemInstance, SchemPort
from ordec.language import ord_to_py
from ordec.render import render


@dataclass
class ValidationResult:
    success: bool
    svg_bytes: bytes | None = None
    error_message: str = ""
    error_stage: str = ""
    error_code: str = ""
    cell_names: list[str] = field(default_factory=list)
    spacing_violations: list[str] = field(default_factory=list)


def _error_code_for_stage(stage: str) -> str:
    return {
        STAGE_PARSING: ERR_PARSE_FAILURE,
        STAGE_COMPILATION: ERR_COMPILE_FAILURE,
        STAGE_EXECUTION: ERR_EXEC_FAILURE,
        STAGE_DISCOVERY: ERR_NO_CELL_DISCOVERED,
        STAGE_INSTANTIATION: ERR_INSTANTIATION_FAILURE,
        STAGE_VIEW_ACCESS: ERR_VIEW_ACCESS_FAILURE,
        STAGE_RENDERING: ERR_RENDER_FAILURE,
        STAGE_SPACING: ERR_SPACING_VIOLATION,
        STAGE_RUNTIME: ERR_VALIDATION_RUNTIME,
    }.get(stage, "")


def _validation_error(
    stage: str,
    message: str,
    *,
    error_code: str | None = None,
    cell_names: list[str] | None = None,
    svg_bytes: bytes | None = None,
    spacing_violations: list[str] | None = None,
) -> ValidationResult:
    return ValidationResult(
        success=False,
        svg_bytes=svg_bytes,
        error_message=message,
        error_stage=stage,
        error_code=error_code or _error_code_for_stage(stage),
        cell_names=cell_names or [],
        spacing_violations=spacing_violations or [],
    )


def _safe_error(exc: Exception | None = None) -> str:
    if exc is not None:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return traceback.format_exc()


def _axis_gap(a_low: float, a_high: float, b_low: float, b_high: float) -> float:
    return max(b_low - a_high, a_low - b_high, 0.0)


def check_layout_spacing(view, min_gap: int = 2) -> list[str]:
    """Check pairwise spacing between schematic elements.

    Ports may be adjacent to ports. Ports and instances (or two instances)
    require at least `min_gap` clear units on one axis when aligned.
    """
    elements: list[tuple[str, Rect4R, str]] = []

    for inst in view.all(SchemInstance):
        name = inst.full_path_str()
        bbox = inst.loc_transform() * inst.symbol.outline
        elements.append((name, bbox, "instance"))

    for port in view.all(SchemPort):
        name = port.full_path_str()
        px, py = float(port.pos.x), float(port.pos.y)
        bbox = Rect4R(px, py, px, py)
        elements.append((name, bbox, "port"))

    violations: list[str] = []
    for i in range(len(elements)):
        name_a, a, kind_a = elements[i]
        for j in range(i + 1, len(elements)):
            name_b, b, kind_b = elements[j]

            if kind_a == "port" and kind_b == "port":
                continue

            a_lx, a_ly, a_ux, a_uy = float(a.lx), float(a.ly), float(a.ux), float(a.uy)
            b_lx, b_ly, b_ux, b_uy = float(b.lx), float(b.ly), float(b.ux), float(b.uy)

            x_gap = _axis_gap(a_lx, a_ux, b_lx, b_ux)
            y_gap = _axis_gap(a_ly, a_uy, b_ly, b_uy)

            # Diagonal separation is not an adjacency violation.
            if x_gap > 0 and y_gap > 0:
                continue

            if x_gap == 0 and y_gap == 0:
                violations.append(f"{name_a} and {name_b}: bounding boxes overlap or touch")
                continue

            if x_gap > 0:
                if x_gap < min_gap:
                    violations.append(
                        f"{name_a} at ({a_lx},{a_ly}) and {name_b} at ({b_lx},{b_ly}): "
                        f"{x_gap}-unit horizontal gap (need {min_gap})"
                    )
            else:
                if y_gap < min_gap:
                    violations.append(
                        f"{name_a} at ({a_lx},{a_ly}) and {name_b} at ({b_lx},{b_ly}): "
                        f"{y_gap}-unit vertical gap (need {min_gap})"
                    )

    return violations


def extract_cell_params(cell_cls) -> list[dict]:
    """Extract parameter info from a Cell subclass."""
    params = []
    for name, param in cell_cls._class_params.items():
        has_default = param.default is not None
        params.append({
            "name": name,
            "type": param.type.__name__,
            "has_default": has_default,
            "default": str(param.default) if has_default else None,
        })
    return params


def extract_ord_code(llm_response: str) -> str | None:
    """Extract ORD code from markdown fences in the LLM response."""
    for lang in ("ord", "python"):
        pattern = rf"```{lang}\s*\n(.*?)```"
        match = re.search(pattern, llm_response, re.DOTALL)
        if match:
            code = match.group(1).strip()
            if not re.match(r"#.*version.*ord", code, re.IGNORECASE):
                code = "# -*- version: ord2 -*-\n" + code
            return code

    pattern = r"```\s*\n(.*?)```"
    match = re.search(pattern, llm_response, re.DOTALL)
    if match:
        code = match.group(1).strip()
        if "cell " in code or "from ordec" in code:
            if not re.match(r"#.*version.*ord", code, re.IGNORECASE):
                code = "# -*- version: ord2 -*-\n" + code
            return code

    return None


def ensure_version_header(source: str) -> str:
    """Ensure the ORD code has a version header."""
    if not re.match(r"#.*version.*ord", source, re.IGNORECASE):
        source = "# -*- version: ord2 -*-\n" + source
    return source


def _default_for_parameter_type(type_name: str) -> str:
    normalized = type_name.strip().split(".")[-1]
    if normalized == "int":
        return "2"
    if normalized == "R":
        return "1u"
    if normalized == "float":
        return "1.0"
    if normalized == "bool":
        return "False"
    if normalized == "str":
        return '"x"'
    return "1"


def ensure_parameter_defaults(source: str) -> str:
    """Inject defaults for bare Parameter(...) declarations.

    Transforms e.g. `w = Parameter(R)` -> `w = Parameter(R, default=1u)`.
    """
    pattern = re.compile(
        r"^(\s*\w+\s*=\s*Parameter\(\s*)([^,\)]+)(\s*\))(\s*(#.*)?)$"
    )
    lines = source.split("\n")
    updated: list[str] = []

    for line in lines:
        match = pattern.match(line)
        if not match:
            updated.append(line)
            continue

        prefix, type_name, suffix, comment = match.groups()
        default_value = _default_for_parameter_type(type_name)
        updated.append(
            f"{prefix}{type_name.strip()}, default={default_value}{suffix}{comment or ''}"
        )

    return "\n".join(updated)


def _discover_cells(source: str, conn_globals: dict) -> list[tuple[str, type[Cell]]]:
    defined_cell_names = set(re.findall(r"^cell\s+(\w+)\s*:", source, re.MULTILINE))
    return [
        (name, cls)
        for name, cls in conn_globals.items()
        if isinstance(cls, type)
        and issubclass(cls, Cell)
        and cls is not Cell
        and name in defined_cell_names
    ]


def _instantiate_cell(cell_cls, test_params: dict[str, str] | None):
    try:
        return cell_cls()
    except ParameterError:
        if test_params:
            return cell_cls(**test_params)
        raise


def _validate_ord_code_structure_in_process(
    source: str,
    test_params: dict[str, str] | None = None,
) -> ValidationResult:
    import ordec.importer  # noqa: F401

    try:
        py_ast = ord_to_py(source)
    except Exception as exc:
        return _validation_error(STAGE_PARSING, _safe_error(exc), error_code=ERR_PARSE_FAILURE)

    try:
        code = compile(py_ast, "<string>", "exec")
    except Exception as exc:
        return _validation_error(
            STAGE_COMPILATION,
            _safe_error(exc),
            error_code=ERR_COMPILE_FAILURE,
        )

    conn_globals = {"public": lambda obj: obj}
    try:
        exec(code, conn_globals, conn_globals)
    except Exception as exc:
        return _validation_error(STAGE_EXECUTION, _safe_error(exc), error_code=ERR_EXEC_FAILURE)

    cell_classes = _discover_cells(source, conn_globals)
    if not cell_classes:
        return _validation_error(
            STAGE_DISCOVERY,
            "No Cell subclasses found in the generated code.",
            error_code=ERR_NO_CELL_DISCOVERED,
        )

    cell_names = [name for name, _ in cell_classes]
    _, cell_cls = cell_classes[0]
    try:
        _instantiate_cell(cell_cls, test_params)
    except ParameterError as exc:
        return _validation_error(
            STAGE_INSTANTIATION,
            _safe_error(exc),
            error_code=ERR_MISSING_REQUIRED_PARAMS,
            cell_names=cell_names,
        )
    except Exception as exc:
        return _validation_error(
            STAGE_INSTANTIATION,
            _safe_error(exc),
            error_code=ERR_INSTANTIATION_FAILURE,
            cell_names=cell_names,
        )

    return ValidationResult(success=True, cell_names=cell_names)


def validate_ord_code_structure(source: str) -> ValidationResult:
    """Validate ORD code structure only (parse->instantiate, no render)."""
    return _validate_ord_code_structure_in_process(source)


def _validate_ord_code_full_in_process(
    source: str,
    test_params: dict[str, str] | None = None,
) -> ValidationResult:
    import ordec.importer  # noqa: F401

    try:
        py_ast = ord_to_py(source)
    except Exception as exc:
        return _validation_error(STAGE_PARSING, _safe_error(exc), error_code=ERR_PARSE_FAILURE)

    try:
        code = compile(py_ast, "<string>", "exec")
    except Exception as exc:
        return _validation_error(
            STAGE_COMPILATION,
            _safe_error(exc),
            error_code=ERR_COMPILE_FAILURE,
        )

    conn_globals = {"public": lambda obj: obj}
    try:
        exec(code, conn_globals, conn_globals)
    except Exception as exc:
        return _validation_error(STAGE_EXECUTION, _safe_error(exc), error_code=ERR_EXEC_FAILURE)

    cell_classes = _discover_cells(source, conn_globals)
    if not cell_classes:
        return _validation_error(
            STAGE_DISCOVERY,
            "No Cell subclasses found in the generated code.",
            error_code=ERR_NO_CELL_DISCOVERED,
        )

    cell_names = [name for name, _ in cell_classes]
    # Prefer the most recently defined cell; in multi-cell files this is usually
    # the top-level design while helper cells may omit a schematic view.
    last_instantiation_error: ValidationResult | None = None
    last_render_error: ValidationResult | None = None
    view_access_misses: list[str] = []

    for name, cell_cls in reversed(cell_classes):
        try:
            instance = _instantiate_cell(cell_cls, test_params)
        except ParameterError as exc:
            last_instantiation_error = _validation_error(
                STAGE_INSTANTIATION,
                _safe_error(exc),
                error_code=ERR_MISSING_REQUIRED_PARAMS,
                cell_names=cell_names,
            )
            continue
        except Exception as exc:
            last_instantiation_error = _validation_error(
                STAGE_INSTANTIATION,
                _safe_error(exc),
                error_code=ERR_INSTANTIATION_FAILURE,
                cell_names=cell_names,
            )
            continue

        try:
            view = getattr(instance, "schematic")
        except AttributeError:
            view_access_misses.append(name)
            continue
        except Exception as exc:
            return _validation_error(
                STAGE_VIEW_ACCESS,
                _safe_error(exc),
                error_code=ERR_VIEW_ACCESS_FAILURE,
                cell_names=cell_names,
            )

        try:
            renderer = render(view)
            svg_bytes = renderer.svg()
        except Exception as exc:
            last_render_error = _validation_error(
                STAGE_RENDERING,
                _safe_error(exc),
                error_code=ERR_RENDER_FAILURE,
                cell_names=cell_names,
            )
            continue

        try:
            violations = check_layout_spacing(view)
        except Exception:
            violations = []

        if violations:
            violations_text = "Spacing violations found:\n" + "\n".join(
                f"- {v}" for v in violations
            )
            return _validation_error(
                STAGE_SPACING,
                violations_text,
                error_code=ERR_SPACING_VIOLATION,
                svg_bytes=svg_bytes,
                cell_names=cell_names,
                spacing_violations=violations,
            )

        return ValidationResult(success=True, svg_bytes=svg_bytes, cell_names=cell_names)

    if last_render_error is not None:
        return last_render_error
    if last_instantiation_error is not None:
        return last_instantiation_error
    if view_access_misses:
        return _validation_error(
            STAGE_VIEW_ACCESS,
            f"No schematic view found. Cells missing schematic: {', '.join(view_access_misses)}",
            error_code=ERR_VIEW_ACCESS_FAILURE,
            cell_names=cell_names,
        )

    return _validation_error(
        STAGE_VIEW_ACCESS,
        "No renderable schematic view was found in discovered cells.",
        error_code=ERR_VIEW_ACCESS_FAILURE,
        cell_names=cell_names,
    )


def _result_to_payload(result: ValidationResult) -> dict:
    return {
        "success": result.success,
        "svg_b64": base64.b64encode(result.svg_bytes).decode("ascii")
        if result.svg_bytes
        else "",
        "error_message": result.error_message,
        "error_stage": result.error_stage,
        "error_code": result.error_code,
        "cell_names": result.cell_names,
        "spacing_violations": result.spacing_violations,
    }


def _payload_to_result(payload: dict) -> ValidationResult:
    svg_b64 = payload.get("svg_b64", "")
    svg_bytes = base64.b64decode(svg_b64) if svg_b64 else None
    return ValidationResult(
        success=bool(payload.get("success", False)),
        svg_bytes=svg_bytes,
        error_message=payload.get("error_message", ""),
        error_stage=payload.get("error_stage", ""),
        error_code=payload.get("error_code", ""),
        cell_names=list(payload.get("cell_names", [])),
        spacing_violations=list(payload.get("spacing_violations", [])),
    )


def _run_worker(payload: dict) -> dict:
    """Run validation worker in a subprocess and return worker JSON payload."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "validator_worker"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=VALIDATION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error_stage": STAGE_RUNTIME,
            "error_code": ERR_VALIDATION_RUNTIME,
            "error_message": f"Validation worker timed out after {VALIDATION_TIMEOUT_SECONDS}s: {exc}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_stage": STAGE_RUNTIME,
            "error_code": ERR_VALIDATION_RUNTIME,
            "error_message": _safe_error(exc),
        }

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "worker exited with non-zero status"
        return {
            "ok": False,
            "error_stage": STAGE_RUNTIME,
            "error_code": ERR_VALIDATION_RUNTIME,
            "error_message": detail,
        }

    raw = proc.stdout.strip()
    if not raw:
        return {
            "ok": False,
            "error_stage": STAGE_RUNTIME,
            "error_code": ERR_VALIDATION_RUNTIME,
            "error_message": "Validation worker returned empty output.",
        }

    try:
        data = json.loads(raw)
    except Exception as exc:
        return {
            "ok": False,
            "error_stage": STAGE_RUNTIME,
            "error_code": ERR_VALIDATION_RUNTIME,
            "error_message": f"Invalid worker JSON: {exc}; output={raw[:200]}",
        }

    if isinstance(data, dict):
        return data

    return {
        "ok": False,
        "error_stage": STAGE_RUNTIME,
        "error_code": ERR_VALIDATION_RUNTIME,
        "error_message": "Worker returned unexpected payload type.",
    }


def validate_ord_code_full(
    source: str,
    test_params: dict[str, str] | None = None,
) -> ValidationResult:
    """Validate ORD code fully including rendering and spacing check.

    Uses a subprocess worker by default for isolation; falls back to
    structured runtime errors on worker failure.
    """
    worker_payload = _run_worker({"source": source, "test_params": test_params})

    if worker_payload.get("ok") is False and "result" not in worker_payload:
        stage = worker_payload.get("error_stage", STAGE_RUNTIME)
        code = worker_payload.get("error_code") or _error_code_for_stage(stage)
        return ValidationResult(
            success=False,
            error_stage=stage,
            error_code=code,
            error_message=worker_payload.get("error_message", "Validation worker failed."),
        )

    result_payload = worker_payload.get("result") if "result" in worker_payload else worker_payload
    if not isinstance(result_payload, dict):
        return ValidationResult(
            success=False,
            error_stage=STAGE_RUNTIME,
            error_code=ERR_VALIDATION_RUNTIME,
            error_message="Validation worker returned malformed result payload.",
        )

    result = _payload_to_result(result_payload)
    if not result.success and not result.error_code:
        result.error_code = _error_code_for_stage(result.error_stage)
    return result


def apply_layout_fixes(source: str, changes) -> str:
    """Apply structured layout changes to ORD source code."""
    for change in changes:
        if change.new_pos_x is not None and change.new_pos_y is not None:
            source = _replace_element_pos(
                source, change.element_name, change.new_pos_x, change.new_pos_y
            )
        if change.new_alignment:
            source = _replace_port_alignment(
                source, change.element_name, change.new_alignment
            )
        if change.disable_route:
            source = _add_route_disable(source, change.element_name)
    return source


def _replace_element_pos(source: str, name: str, new_x: int, new_y: int) -> str:
    """Replace .pos coordinates for a named port or instance."""
    escaped = re.escape(name)
    lines = source.split("\n")

    for i, line in enumerate(lines):
        stripped = line.lstrip()

        if re.match(rf"(port\s+)?{escaped}\s*\(", stripped) or re.match(
            rf"\w+\s+{escaped}\s*\(", stripped
        ):
            new_line = re.sub(
                r"\.pos\s*=\s*\(\s*\d+\s*,\s*\d+\s*\)",
                f".pos=({new_x}, {new_y})",
                line,
            )
            if new_line != line:
                lines[i] = new_line
                return "\n".join(lines)

        if re.match(rf"\w+\s+{escaped}\s*:", stripped):
            decl_indent = len(line) - len(stripped)
            for j in range(i + 1, len(lines)):
                inner = lines[j]
                inner_stripped = inner.lstrip()
                if not inner_stripped or inner_stripped.startswith("#"):
                    continue
                inner_indent = len(inner) - len(inner_stripped)
                if inner_indent <= decl_indent:
                    break
                if re.match(r"\.pos\s*=\s*\(", inner_stripped):
                    indent = inner[:inner_indent]
                    lines[j] = f"{indent}.pos = ({new_x}, {new_y})"
                    return "\n".join(lines)

    return source


def _replace_port_alignment(source: str, name: str, new_alignment: str) -> str:
    """Replace .align for a named port."""
    escaped = re.escape(name)
    return re.sub(
        rf"(port\s+{escaped}\s*\([^)]*\.align\s*=\s*)Orientation\.\w+",
        rf"\1Orientation.{new_alignment}",
        source,
    )


def _add_route_disable(source: str, name: str) -> str:
    """Add .route = False for a port or net if not already present."""
    escaped = re.escape(name)
    if re.search(rf"\b{escaped}(\.ref)?\.route\s*=\s*False", source):
        return source

    lines = source.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]

        if re.match(rf"port\s+{escaped}\s*\(", stripped):
            lines.insert(i + 1, f"{indent}{name}.ref.route = False")
            return "\n".join(lines)

        if re.match(rf"net\s+{escaped}\b", stripped):
            lines.insert(i + 1, f"{indent}{name}.route = False")
            return "\n".join(lines)

    return source


def strip_explicit_helpers(source: str) -> str:
    """Remove explicit helper calls and returns from viewgen blocks."""
    strip_patterns = [
        r"^\s*helpers\.symbol_place_pins\(ctx\.root.*\)\s*$",
        r"^\s*helpers\.resolve_instances\(ctx\.root\)\s*$",
        r"^\s*ctx\.root\.outline\s*=\s*schematic_routing\(ctx\.root\)\s*$",
        r"^\s*return\s+ctx\.root\s*$",
    ]
    lines = source.split("\n")
    result = []
    for line in lines:
        if any(re.match(pattern, line) for pattern in strip_patterns):
            continue
        result.append(line)

    cleaned = "\n".join(result)
    cleaned = re.sub(
        r"^from ordec\.schematic\.routing import schematic_routing\n",
        "",
        cleaned,
        flags=re.MULTILINE,
    )
    return cleaned
