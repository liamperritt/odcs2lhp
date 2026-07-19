# odcs2lhp

Translate [ODCS](https://bitol.io/) (Open Data Contract Standard) data contracts
into [Lakehouse Plumber](https://github.com/Mmodarre/Lakehouse_Plumber) YAML
**sidecar files**, so LHP pipelines can reference contract-derived schemas, tags,
and expectations directly.

`odcs2lhp` is a standalone package. It reads only your ODCS contract files and your
project's `lhp.yaml`. It never inspects pipeline YAMLs or any other files.

## Install

```bash
pip install -e .   # from this directory
```

## Usage

```bash
# from your LHP project root (default contracts dir is ./contracts)
odcs2lhp translate

# custom contracts directory
odcs2lhp translate --contracts-dir data_contracts

# other options
odcs2lhp translate --project-root /path/to/project -v
```

Each run wipes and rebuilds the output directory, so the sidecars are always a
fresh reflection of your contracts.

`odcs2lhp translate` runs *before* `lhp validate` / `lhp generate`:

```bash
odcs2lhp translate && lhp validate --env dev && lhp generate --env dev
```

## What it writes

For every schema object in every discovered contract, five sidecars are written
under `.lhp/odcs/` (which LHP already gitignores). The path mirrors the contract
file's location under the contracts dir plus its filename without extension —
`<prefix>` — so each contract's output tree is unique (e.g.
`contracts/marketing/sales.contract.yaml` -> prefix `marketing/sales.contract`).
The contract version lives in the file content, not the path.

| Sidecar | Path | Referenced from a pipeline action via |
|---|---|---|
| Load schema | `<prefix>/load/schemas/<obj>_schema.yaml` | `source.schema` / `cloudFiles.schemaHints` on a cloudFiles load |
| Transform schema | `<prefix>/transform/schemas/<obj>_transform.yaml` | `schema_file` on a `transform_type: schema` action |
| Expectations | `<prefix>/transform/expectations/<obj>_expectations.yaml` | `expectations_file` on a `transform_type: data_quality` action |
| Write schema | `<prefix>/write/schemas/<obj>_schema.yaml` | `write_target.table_schema` on a write action |
| Table tags | `<prefix>/write/tags/<obj>_tags.yaml` | *(table-level tags — planned LHP support)* |

For example, `marketing/sales.contract/write/schemas/customer_schema.yaml`.

### Details

- **Load** and **transform** schemas exclude operational-metadata columns (read
  from `operational_metadata.columns` in `lhp.yaml`) and the SCD2 columns
  `__START_AT` / `__END_AT`: these are injected by LHP, not sourced from the input
  data. The **write** schema keeps every column.
- **Load** columns are named by their ODCS `physicalName` (the source column name);
  **transform** and **write** schemas use the contract (logical) names.
- **Expectations** combine `required: true` -> `<col> IS NOT NULL` with each
  property's `logicalTypeOptions` predicates. `failureAction` is `fail` for a
  `criticalDataElement` property, else `warn`.
- **Column** UC tags ride on the write schema; **table** UC tags go in the tags
  file. Tag strings use the `key:value` convention (colon-less -> key-only tag).

## Example pipeline references

```yaml
- name: load_customer
  type: load
  source:
    type: cloudfiles
    path: ${landing}/customer/*.json
    format: json
    schema: .lhp/odcs/schemas/load/sales__customer_schema.yaml
  target: v_customer_raw

- name: cast_customer
  type: transform
  transform_type: schema
  source: v_customer_raw
  target: v_customer_mapped
  schema_file: .lhp/odcs/schemas/transform/sales__customer_schema.yaml

- name: validate_customer
  type: transform
  transform_type: data_quality
  source: v_customer_mapped
  target: v_customer_validated
  expectations_file: .lhp/odcs/expectations/sales__customer_expectations.yaml

- name: write_customer
  type: write
  source: v_customer_validated
  write_target:
    type: streaming_table
    catalog: ${catalog}
    schema: ${bronze_schema}
    table: customer
    table_schema: .lhp/odcs/schemas/write/sales__customer_schema.yaml
```
