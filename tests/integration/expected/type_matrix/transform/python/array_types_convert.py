"""logicalType array, JSON-string parse + structured physicals + fallbacks"""
from pyspark.sql import DataFrame, functions as F


def convert_types(df: DataFrame, spark, parameters: dict) -> DataFrame:
    df = df.withColumn("a_json", F.expr("from_json(`a_json`, 'ARRAY<STRING>')"))
    return df
