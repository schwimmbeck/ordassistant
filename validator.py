import re
import sys
import traceback
from dataclasses import dataclass, field

from ordec.core.cell import Cell
from ordec.language import ord_to_py
from ordec.render import render


@dataclass
class ValidationResult:
    success: bool
    svg_bytes: bytes | None = None
    error_message: str = ""
    error_stage: str = ""
    cell_names: list[str] = field(default_factory=list)


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


def validate_ord_code(source: str) -> ValidationResult:
    """Validate ORD code by parsing, compiling, executing, and rendering.

    Mirrors the ordec server.py build_cells() pipeline.
    """
    # Ensure the ORD importer is registered
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
    # Pre-inject `public` so LLM-generated @public decorators work silently
    conn_globals = {"public": lambda obj: obj}
    try:
        exec(code, conn_globals, conn_globals)
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="execution",
        )

    # Stage 4: Discover Cell subclasses defined in this source (not imports)
    # Extract cell names from the ORD source to filter out imported cells
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

    # Stage 5 & 6: Instantiate and render the first cell's view
    name, cell_cls = cell_classes[0]
    try:
        instance = cell_cls()
    except Exception:
        return ValidationResult(
            success=False,
            error_message=traceback.format_exc(),
            error_stage="instantiation",
            cell_names=cell_names,
        )

    # Only use the schematic view
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

    return ValidationResult(
        success=True,
        svg_bytes=svg_bytes,
        cell_names=cell_names,
    )
