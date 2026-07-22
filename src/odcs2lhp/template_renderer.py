"""Render bundled Jinja2 templates into generated Python transform modules.

Named to cohere with LHP's ``lhp/core/codegen/template_renderer.py`` (odcs2lhp is
intended to be contributed back into LHP). Owns the Jinja2 ``Environment`` so the
rest of the package (``translator``, ``mapper``) stays free of templating I/O.
"""

from __future__ import annotations

from typing import List

from jinja2 import Environment, PackageLoader

from .mapper import Conversion

# Function name for the generated per-object type-conversion transform. A
# constant is safe because each object gets its own module.
TYPE_CONVERT_FUNCTION = "convert_types"


class TemplateRenderer:
    """Render odcs2lhp's bundled ``*.py.j2`` templates.

    A single ``Environment`` (block-trimming, no auto-reload) mirrors LHP's
    generator setup so output stays byte-stable and templates are loaded from the
    installed package rather than a filesystem path.
    """

    def __init__(self) -> None:
        self._env = Environment(  # nosec B701 — generates Python, not HTML
            loader=PackageLoader("odcs2lhp", "templates"),
            trim_blocks=True,
            lstrip_blocks=True,
            auto_reload=False,
            keep_trailing_newline=True,
        )

    def render_type_convert(
        self,
        *,
        object_name: str,
        description: str,
        conversions: List[Conversion],
        function_name: str = TYPE_CONVERT_FUNCTION,
    ) -> str:
        """Render the type-conversion transform module for one schema object.

        With no ``conversions`` the module is a passthrough (``return df``);
        otherwise it applies one ``F.expr`` per conversion, in order.
        """
        template = self._env.get_template("type_convert.py.j2")
        return template.render(
            object_name=object_name,
            description=description,
            function_name=function_name,
            conversions=conversions,
        )
