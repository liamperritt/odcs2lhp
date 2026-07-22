"""Marketing campaigns"""
from pyspark.sql import DataFrame, functions as F


def convert_types(df: DataFrame, spark, parameters: dict) -> DataFrame:
    df = df.withColumn("launch_date", F.expr("to_date(`launch_date`, 'MM/dd/yyyy')"))
    df = df.withColumn("created_at", F.expr("to_timestamp(`created_at`, 'yyyy-MM-dd''T''HH:mm:ss')"))
    df = df.withColumn("updated_at", F.expr("to_utc_timestamp(to_timestamp(`updated_at`, 'yyyy-MM-dd HH:mm:ss'), 'America/New_York')"))
    df = df.withColumn("audience", F.expr("from_json(`audience`, 'STRUCT<segment:STRING,size:INT>')"))
    df = df.withColumn("channels", F.expr("from_json(`channels`, 'ARRAY<STRING>')"))
    df = df.withColumn("raw_payload", F.expr("parse_json(`raw_payload`)"))
    df = df.withColumn("tracking_pixel", F.expr("unbase64(`tracking_pixel`)"))
    return df
