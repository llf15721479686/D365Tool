# D365 Field Provisioning Tool

This tool auto-creates D365 fields from `field_schema.json` and validates names/types with strict CRM format rules.

## 1) C# / WinForm Reuse

You can directly reuse `FieldProvisioningTool` in WinForm code:

```csharp
var secretJson = File.ReadAllText("config.json");
var secret = JsonSerializerHelper.Deserialize<Secret>(secretJson);
SetupService.Execute();
var tool = new FieldProvisioningTool();
tool.Do(secret);
```

### Run in this project

1. Set `TaskCode = 3` in `config.json`
2. Prepare `field_schema.json`
3. Run `D365Tool.exe`

## 2) Python Reuse

Script file: `d365_field_creator.py`

```bash
pip install -r requirements.txt
python d365_field_creator.py
```

Edit auth values in the script:

- `tenant_id`
- `client_id`
- `client_secret`
- `org_url`

## 3) Strict validation rules

- `entity_logical_name` must match: `^[a-z][a-z0-9_]{2,49}$`
- `logical_name` must match: `^[a-z][a-z0-9_]{2,49}$`
- if `publisher_prefix` exists, `logical_name` must start with `{prefix}_`
- `schema_name` must match: `^[A-Za-z][A-Za-z0-9]{2,79}$`
- duplicate `logical_name` is blocked
- unsupported `field_type` is blocked

Supported field types:

- `string`
- `memo`
- `integer`
- `decimal`
- `double`
- `money`
- `datetime`
- `boolean`
- `picklist`
- `lookup`

## 4) Package for other users (Windows)

This project provides `build_release.bat` for one-click packaging.

### Build

```bat
build_release.bat
```

### Output

After build, a `release` folder will be generated:

- `D365FieldCreator.exe` (GUI app)
- `config.json` (from template, fill your own values)
- `schema.json` (sample schema)
- `README_FieldProvisioning.md`

### Notes for receivers

1. Edit `config.json` and fill real auth values.
2. Keep `schema_file` pointing to `schema.json` (or your own json path).
3. Double click `D365FieldCreator.exe` to start GUI.
