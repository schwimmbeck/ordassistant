import re
import traceback
from dataclasses import dataclass, field

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
    cell_names: list[str] = field(default_factory=list)
    spacing_violations: list[str] = field(default_factory=list)


def check_layout_spacing(view, min_gap: int = 2) -> list[str]:
    """Check pairwise bounding-box gaps between all schematic elements.

    Checks instances (subcells/transistors) AND ports. Returns a list of
    violation strings. An empty list means no violations.
    """
    # Collect all elements with their names and schematic-space bounding boxes
    elements: list[tuple[str, Rect4R]] = []

    for inst in view.all(SchemInstance):
        name = inst.full_path_str()
        bbox = inst.loc_transform() * inst.symbol.outline
        elements.append((name, bbox))

    for port in view.all(SchemPort):
        name = port.full_path_str()
        px, py = float(port.pos.x), float(port.pos.y)
        bbox = Rect4R(px, py, px, py)  # Point — zero-size bbox
        elements.append((name, bbox))

    violations: list[str] = []
    for i in range(len(elements)):
        name_a, a = elements[i]
        for j in range(i + 1, len(elements)):
            name_b, b = elements[j]

            # Check axis-aligned projections for overlap
            x_overlap = a.lx < b.ux and b.lx < a.ux
            y_overlap = a.ly < b.uy and b.ly < a.uy

            if x_overlap and y_overlap:
                violations.append(
                    f"{name_a} and {name_b}: bounding boxes overlap"
                )
                continue

            if not x_overlap and not y_overlap:
                # Diagonally separated — not adjacent, skip
                continue

            # One axis overlaps, check gap on the other
            if x_overlap:
                # Projections overlap on X → check vertical gap
                gap = max(float(b.ly - a.uy), float(a.ly - b.uy))
                if gap < min_gap:
                    violations.append(
                        f"{name_a} at ({float(a.lx)},{float(a.ly)}) and "
                        f"{name_b} at ({float(b.lx)},{float(b.ly)}): "
                        f"{gap}-unit vertical gap (need {min_gap})"
                    )
            else:
                # Projections overlap on Y → check horizontal gap
                gap = max(float(b.lx - a.ux), float(a.lx - b.ux))
                if gap < min_gap:
                    violations.append(
                        f"{name_a} at ({float(a.lx)},{float(a.ly)}) and "
                        f"{name_b} at ({float(b.lx)},{float(b.ly)}): "
                        f"{gap}-unit horizontal gap (need {min_gap})"
                    )

    return violations


def extract_cell_params(cell_cls) -> list[dict]:
    """Extract parameter info from a Cell subclass.

    Returns a list of dicts with keys: name, type, has_default, default.
    """
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
    """Extract ORD code from markdown fences in the LLM response.

    Looks for ```ord or ```python fenced code blocks.
    Returns the code with a version header if missing, or None if no code found.
    """
    # Try ```ord first, then ```python
    for lang in ("ord", "python"):
        pattern = rf"```{lang}\s*\n(.*?)```"
        match = re.search(pattern, llm_response, re.DOTALL)
        if match:
            code = match.group(1).strip()
            if not re.match(r"#.*version.*ord", code, re.IGNORECASE):
                code = "# -*- version: ord2 -*-\n" + code
            return code

    # Try bare ``` fences as fallback
    pattern = r"```\s*\n(.*?)```"
    match = re.search(pattern, llm_response, re.DOTALL)
    if match:
        code = match.group(1).strip()
        # Only accept if it looks like ORD code
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


def _run_stages_1_to_5(source: str) -> ValidationResult:
    """Run validation stages 1-5 (parse, compile, execute, discover, instantiate).

    Returns a ValidationResult. On success, cell_names is populated but
    svg_bytes is None (rendering not attempted).
    """
    import ordec.importer  # noqa: F401

    # Stage 1: Parse ORD to Python AST
    try:
        py_ast = ord_to_py(source)
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="parsing",
        )

    # Stage 2: Compile AST to bytecode
    try:
        code = compile(py_ast, "<string>", "exec")
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="compilation",
        )

    # Stage 3: Execute to define Cell classes
    conn_globals = {"public": lambda obj: obj}
    try:
        exec(code, conn_globals, conn_globals)
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="execution",
        )

    # Stage 4: Discover Cell subclasses defined in this source
    defined_cell_names = set(re.findall(r"^cell\s+(\w+)\s*:", source, re.MULTILINE))
    cell_classes = [
        (name, cls)
        for name, cls in conn_globals.items()
        if isinstance(cls, type) and issubclass(cls, Cell) and cls is not Cell
        and name in defined_cell_names
    ]

    if not cell_classes:
        return ValidationResult(
            success=False,
            error_message="No Cell subclasses found in the generated code.",
            error_stage="discovery",
        )

    cell_names = [name for name, _ in cell_classes]

    # Stage 5: Instantiate the first cell
    name, cell_cls = cell_classes[0]
    try:
        cell_cls()
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="instantiation",
            cell_names=cell_names,
        )

    return ValidationResult(
        success=True,
        cell_names=cell_names,
    )


def validate_ord_code_structure(source: str) -> ValidationResult:
    """Validate ORD code structure only (stages 1-5, no rendering)."""
    return _run_stages_1_to_5(source)


def validate_ord_code_full(
    source: str, test_params: dict[str, str] | None = None
) -> ValidationResult:
    """Validate ORD code fully including rendering and spacing check.

    Args:
        source: ORD source code to validate.
        test_params: Optional parameter values for parameterized cells.
            If provided and default instantiation fails with ParameterError,
            retries with these values.
    """
    import ordec.importer  # noqa: F401

    # Stage 1: Parse ORD to Python AST
    try:
        py_ast = ord_to_py(source)
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="parsing",
        )

    # Stage 2: Compile AST to bytecode
    try:
        code = compile(py_ast, "<string>", "exec")
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="compilation",
        )

    # Stage 3: Execute to define Cell classes
    conn_globals = {"public": lambda obj: obj}
    try:
        exec(code, conn_globals, conn_globals)
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="execution",
        )

    # Stage 4: Discover Cell subclasses
    defined_cell_names = set(re.findall(r"^cell\s+(\w+)\s*:", source, re.MULTILINE))
    cell_classes = [
        (name, cls)
        for name, cls in conn_globals.items()
        if isinstance(cls, type) and issubclass(cls, Cell) and cls is not Cell
        and name in defined_cell_names
    ]

    if not cell_classes:
        return ValidationResult(
            success=False,
            error_message="No Cell subclasses found in the generated code.",
            error_stage="discovery",
        )

    cell_names = [name for name, _ in cell_classes]

    # Stage 5: Instantiate
    name, cell_cls = cell_classes[0]
    try:
        instance = cell_cls()
    except ParameterError:
        if test_params:
            try:
                instance = cell_cls(**test_params)
            except Exception:
                return ValidationResult(
                    success=False,
                    error_message=traceback.format_exc(),
                    error_stage="instantiation",
                    cell_names=cell_names,
                )
        else:
            return ValidationResult(
                success=False,
                error_message=traceback.format_exc(),
                error_stage="instantiation",
                cell_names=cell_names,
            )
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="instantiation",
            cell_names=cell_names,
        )

    # Stage 6a: Access schematic view
    try:
        view = getattr(instance, "schematic")
    except AttributeError:
        return ValidationResult(
            success=False,
            error_message=f"Cell '{name}' has no schematic viewgen.",
            error_stage="view_access",
            cell_names=cell_names,
        )
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="view_access",
            cell_names=cell_names,
        )

    # Stage 6b: Render to SVG
    try:
        renderer = render(view)
        svg_bytes = renderer.svg()
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="rendering",
            cell_names=cell_names,
        )

    # Stage 7: Check layout spacing
    try:
        violations = check_layout_spacing(view)
    except Exception:
        # Non-fatal: if spacing check itself errors, just skip it
        violations = []

    if violations:
        violations_text = "Spacing violations found:\n" + "\n".join(
            f"- {v}" for v in violations
        )
        return ValidationResult(
            success=False,
            error_stage="spacing",
            error_message=violations_text,
            svg_bytes=svg_bytes,
            cell_names=cell_names,
            spacing_violations=violations,
        )

    return ValidationResult(
        success=True,
        svg_bytes=svg_bytes,
        cell_names=cell_names,
    )


def apply_layout_fixes(source: str, changes) -> str:
    """Apply structured layout changes to ORD source code.

    Handles position changes, alignment changes, and route disabling
    for ports and instances. Returns modified source.
    """
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

        # Inline declaration: port name(...) or Type name(...)
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

        # Block declaration: Type name:
        if re.match(rf"\w+\s+{escaped}\s*:", stripped):
            decl_indent = len(line) - len(stripped)
            for j in range(i + 1, len(lines)):
                inner = lines[j]
                inner_stripped = inner.lstrip()
                if not inner_stripped or inner_stripped.startswith("#"):
                    continue
                inner_indent = len(inner) - len(inner_stripped)
                if inner_indent <= decl_indent:
                    break  # Left the block
                if re.match(r"\.pos\s*=\s*\(", inner_stripped):
                    indent = inner[:inner_indent]
                    lines[j] = f"{indent}.pos = ({new_x}, {new_y})"
                    return "\n".join(lines)

    return source  # Element not found, return unchanged


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
        return source  # Already disabled

    lines = source.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]

        # Port declaration
        if re.match(rf"port\s+{escaped}\s*\(", stripped):
            lines.insert(i + 1, f"{indent}{name}.ref.route = False")
            return "\n".join(lines)

        # Net declaration
        if re.match(rf"net\s+{escaped}\b", stripped):
            lines.insert(i + 1, f"{indent}{name}.route = False")
            return "\n".join(lines)

    return source  # Element not found


def strip_explicit_helpers(source: str) -> str:
    """Remove explicit helper calls and returns from viewgen blocks.

    Strips the lines that were added to bypass implicit schem_check:
      - helpers.symbol_place_pins(ctx.root, vpadding=2, hpadding=2)
      - helpers.resolve_instances(ctx.root)
      - ctx.root.outline = schematic_routing(ctx.root)
      - return ctx.root

    Also removes the schematic_routing import if no longer needed.
    After stripping, the implicit context behavior (with schem_check)
    takes over.
    """
    _STRIP_PATTERNS = [
        r"^\s*helpers\.symbol_place_pins\(ctx\.root.*\)\s*$",
        r"^\s*helpers\.resolve_instances\(ctx\.root\)\s*$",
        r"^\s*ctx\.root\.outline\s*=\s*schematic_routing\(ctx\.root\)\s*$",
        r"^\s*return\s+ctx\.root\s*$",
    ]
    lines = source.split("\n")
    result = []
    for line in lines:
        if any(re.match(pat, line) for pat in _STRIP_PATTERNS):
            continue
        result.append(line)

    # Remove the schematic_routing import line if present
    cleaned = "\n".join(result)
    cleaned = re.sub(
        r"^from ordec\.schematic\.routing import schematic_routing\n",
        "",
        cleaned,
        flags=re.MULTILINE,
    )

    return cleaned
