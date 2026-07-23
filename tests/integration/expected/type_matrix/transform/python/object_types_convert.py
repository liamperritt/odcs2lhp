"""logicalType object, JSON-string parse + structured physicals + fallbacks"""
from pyspark.sql import DataFrame, functions as F


def convert_types(df: DataFrame, spark, parameters: dict) -> DataFrame:
    df = df.withColumn("o_json_struct", F.expr("from_json(`o_json_struct`, 'STRUCT<seg:STRING,sz:INT>')"))
    df = df.withColumn("o_json_variant", F.expr("parse_json(`o_json_variant`)"))
    return df
