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
| UC tags | `<prefix>/write/uc_tags/<obj>_tags.yaml` | *(table-level + per-column UC tags)* |

For example, `marketing/sales.contract/write/schemas/customer_schema.yaml`.

### Details

- **Load** and **transform** schemas exclude operational-metadata columns (read
  from `operational_metadata.columns` in `lhp.yaml`) and the SCD2 columns
  `__START_AT` / `__END_AT`: these are injected by LHP, not sourced from the input
  data. The **write** schema keeps every column.
- **Load** columns are named by their ODCS `physicalName` (the source column name);
  **transform** and **write** schemas use the contract (logical) names.
- **Type casting** only emits what a bare Spark `cast()` can do. Both `physicalType`
  and `logicalType` are required on every property (and on nested `properties`/`items`);
  a property missing either fails with `ODCS-TYPE-001`.
- **Expectations** combine `required: true` -> `<col> IS NOT NULL` with each
  property's `logicalTypeOptions` predicates. `failureAction` is `fail` for a
  `criticalDataElement` property, else `warn`. Deferred string-encoded columns emit
  no `logicalTypeOptions` predicates (their shape/bound checks can't run against the
  unconverted string); the `required` NOT NULL check still applies.
- **UC tags** all live in the `write/uc_tags/<obj>_tags.yaml` file: table-level tags
  under `tags`, and per-column tags under `columns` (one `{name, tags}` entry per
  column, `tags: {}` when none). Contract-level tags form the base applied to every
  table, and an object-level tag of the same key overrides the contract value. Tag
  strings use the `key:value` convention (colon-less -> key-only tag).

## Example pipeline references

```yaml
- name: load_customer
  type: load
  source:
    type: cloudfiles
    path: ${landing}/customer/*.json
    format: json
    schema: .lhp/odcs/sales.contract/load/schemas/customer_schema.yaml
  target: v_customer_raw

- name: cast_customer
  type: transform
  transform_type: schema
  source: v_customer_raw
  target: v_customer_mapped
  schema_file: .lhp/odcs/sales.contract/transform/schemas/customer_transform.yaml

- name: validate_customer
  type: transform
  transform_type: data_quality
  source: v_customer_mapped
  target: v_customer_validated
  expectations_file: .lhp/odcs/sales.contract/transform/expectations/customer_expectations.yaml

- name: write_customer
  type: write
  source: v_customer_validated
  write_target:
    type: streaming_table
    catalog: ${catalog}
    schema: ${bronze_schema}
    table: customer
    table_schema: .lhp/odcs/sales.contract/write/schemas/customer_schema.yaml
```
