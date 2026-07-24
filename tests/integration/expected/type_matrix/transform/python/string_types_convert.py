"""logicalType string, across usable/unusable physicals + formats"""
from pyspark.sql import DataFrame, functions as F


def convert_types(df: DataFrame, spark, parameters: dict) -> DataFrame:
    df = df.withColumn("s_base64", F.expr("unbase64(`s_base64`)"))
    return df
