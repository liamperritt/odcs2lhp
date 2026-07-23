"""logicalType integer, all Rust widths + physical reconciliation"""
from pyspark.sql import DataFrame, functions as F


def convert_types(df: DataFrame, spark, parameters: dict) -> DataFrame:
    return df
