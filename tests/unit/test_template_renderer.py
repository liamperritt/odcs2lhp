"""Behaviour tests for :class:`odcs2lhp.template_renderer.TemplateRenderer`.

Assert on the exact rendered module text so template whitespace/formatting
regressions are caught (the generated ``.py`` is committed as an integration
golden and must stay byte-stable).
"""

from __future__ import annotations

from odcs2lhp.mapper import Conversion
from odcs2lhp.template_renderer import TemplateRenderer


def _render(conversions, description="Type conversions for customer"):
    return TemplateRenderer().render_type_convert(
        object_name="customer",
        description=description,
        conversions=conversions,
    )


def test_render_type_convert_emits_passthrough_when_no_conversions():
    rendered = _render([])

    assert rendered == (
        '"""Type conversions for customer"""\n'
        "from pyspark.sql import DataFrame, functions as F\n"
        "\n"
        "\n"
        "def convert_types(df: DataFrame, spark, parameters: dict) -> DataFrame:\n"
        "    return df\n"
    )


def test_render_type_convert_emits_one_with_column_per_conversion():
    conversions = [
        Conversion(
            kind="to_timestamp",
            column="created_at",
            source_type="STRING",
            target_type="TIMESTAMP",
            sql_expr="to_timestamp(`created_at`, 'yyyy-MM-dd''T''HH:mm:ss')",
        ),
        Conversion(
            kind="parse_json",
            column="meta",
            source_type="STRING",
            target_type="VARIANT",
            sql_expr="parse_json(`meta`)",
        ),
    ]

    rendered = _render(conversions)

    assert rendered == (
        '"""Type conversions for customer"""\n'
        "from pyspark.sql import DataFrame, functions as F\n"
        "\n"
        "\n"
        "def convert_types(df: DataFrame, spark, parameters: dict) -> DataFrame:\n"
        "    df = df.withColumn(\"created_at\", "
        "F.expr(\"to_timestamp(`created_at`, 'yyyy-MM-dd''T''HH:mm:ss')\"))\n"
        "    df = df.withColumn(\"meta\", F.expr(\"parse_json(`meta`)\"))\n"
        "    return df\n"
    )
