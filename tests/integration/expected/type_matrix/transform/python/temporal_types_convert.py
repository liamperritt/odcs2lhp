"""logicalType date/timestamp/time, format-aware conversions + fallbacks"""
from pyspark.sql import DataFrame, functions as F


def convert_types(df: DataFrame, spark, parameters: dict) -> DataFrame:
    df = df.withColumn("d_string_format", F.expr("to_date(`d_string_format`, 'MM/dd/yyyy')"))
    df = df.withColumn("ts_string_format", F.expr("to_timestamp(`ts_string_format`, 'yyyy-MM-dd''T''HH:mm:ss')"))
    df = df.withColumn("ts_string_tz", F.expr("to_utc_timestamp(to_timestamp(`ts_string_tz`, 'yyyy-MM-dd HH:mm:ss'), 'America/New_York')"))
    df = df.withColumn("t_string", F.expr("to_timestamp(`t_string`, 'HH:mm:ss')"))
    return df
