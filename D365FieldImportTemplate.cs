using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace D365Tool.Template
{
    /// <summary>
    /// D365 字段导入模板（骨架版）
    ///
    /// 你可以把字段定义放到 JSON，然后通过本模板统一导入到对应实体。
    /// 本文件重点是“结构和规则”，D365 实际 SDK/API 调用请在 TODO 区替换。
    /// </summary>
    public static class D365FieldImportTemplate
    {
        public static void ImportFromJson(string jsonPath)
        {
            PaymentGate.EnsureAccessGranted();

            if (!File.Exists(jsonPath))
            {
                throw new FileNotFoundException($"Schema file not found: {jsonPath}");
            }

            var json = File.ReadAllText(jsonPath);
            var options = new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
                ReadCommentHandling = JsonCommentHandling.Skip,
                AllowTrailingCommas = true
            };

            var schema = JsonSerializer.Deserialize<FieldImportSchema>(json, options)
                         ?? throw new InvalidOperationException("Schema parse failed.");

            FieldSchemaValidator.Validate(schema);

            var importer = new D365FieldImporter();
            importer.Import(schema);
        }
    }

    public static class PaymentGate
    {
        private const int RequiredAmount = 30;
        private const string ConfirmPhrase = "我已请你喝奶茶";
        private const string UnlockFileName = ".d365tool-payment-unlock.json";

        // 支持本地路径或 http/https 图片地址
        private const string PaymentQrCodeImagePath =
            "https://mp-22e7468a-898b-4fd0-b8ef-c58cd290ba45.cdn.bspapp.com/图片/pay.jpg";

        public static void EnsureAccessGranted()
        {
            if (IsUnlocked())
            {
                return;
            }

            Console.WriteLine("==========================================");
            Console.WriteLine($"请先扫码支付 {RequiredAmount} 元，才能继续使用。");
            Console.WriteLine("提示语：请喝奶茶，才能使用，哈哈哈哈");
            Console.WriteLine("==========================================");
            Console.WriteLine($"收款码：{PaymentQrCodeImagePath}");

            TryOpenQrCode(PaymentQrCodeImagePath);

            Console.WriteLine();
            Console.Write($"支付完成后请输入“{ConfirmPhrase}”继续：");
            var confirmText = Console.ReadLine()?.Trim();

            if (!string.Equals(confirmText, ConfirmPhrase, StringComparison.Ordinal))
            {
                throw new InvalidOperationException("未完成支付验证，已停止执行。");
            }

            SaveUnlockReceipt();
            Console.WriteLine("支付验证成功，已解锁当前工具。");
        }

        private static bool IsUnlocked()
        {
            var unlockFilePath = GetUnlockFilePath();
            if (!File.Exists(unlockFilePath))
            {
                return false;
            }

            try
            {
                var unlockContent = File.ReadAllText(unlockFilePath);
                var receipt = JsonSerializer.Deserialize<PaymentReceipt>(unlockContent);
                return receipt?.Paid == true && receipt.Amount == RequiredAmount;
            }
            catch
            {
                return false;
            }
        }

        private static void SaveUnlockReceipt()
        {
            var receipt = new PaymentReceipt
            {
                Paid = true,
                Amount = RequiredAmount,
                ConfirmedAtUtc = DateTime.UtcNow
            };

            var unlockContent = JsonSerializer.Serialize(receipt, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(GetUnlockFilePath(), unlockContent);
        }

        private static string GetUnlockFilePath()
        {
            return Path.Combine(AppContext.BaseDirectory, UnlockFileName);
        }

        private static void TryOpenQrCode(string qrCodePathOrUrl)
        {
            var isWebUrl = Uri.TryCreate(qrCodePathOrUrl, UriKind.Absolute, out var uri) &&
                           (uri!.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps);

            if (!isWebUrl && !File.Exists(qrCodePathOrUrl))
            {
                return;
            }

            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = qrCodePathOrUrl,
                    UseShellExecute = true
                });
            }
            catch
            {
                // 打开失败时，终端里已经有路径提示，用户仍可手动扫码。
            }
        }

        private sealed class PaymentReceipt
        {
            [JsonPropertyName("paid")]
            public bool Paid { get; set; }

            [JsonPropertyName("amount")]
            public int Amount { get; set; }

            [JsonPropertyName("confirmed_at_utc")]
            public DateTime ConfirmedAtUtc { get; set; }
        }
    }

    #region Schema Models

    public sealed class FieldImportSchema
    {
        [JsonPropertyName("publisher_prefix")]
        public string PublisherPrefix { get; set; } = "mcs";

        [JsonPropertyName("solutions")]
        public List<SolutionDefinition> Solutions { get; set; } = new();
    }

    public sealed class SolutionDefinition
    {
        [JsonPropertyName("solution_unique_name")]
        public string SolutionUniqueName { get; set; } = string.Empty;

        [JsonPropertyName("entities")]
        public List<EntityDefinition> Entities { get; set; } = new();
    }

    public sealed class EntityDefinition
    {
        [JsonPropertyName("entity_logical_name")]
        public string EntityLogicalName { get; set; } = string.Empty;

        [JsonPropertyName("fields")]
        public List<FieldDefinition> Fields { get; set; } = new();
    }

    public sealed class FieldDefinition
    {
        [JsonPropertyName("logical_name")]
        public string LogicalName { get; set; } = string.Empty;

        [JsonPropertyName("schema_name")]
        public string SchemaName { get; set; } = string.Empty;

        [JsonPropertyName("display_name")]
        public string DisplayName { get; set; } = string.Empty;

        [JsonPropertyName("description")]
        public string Description { get; set; } = string.Empty;

        [JsonPropertyName("required_level")]
        [JsonConverter(typeof(JsonStringEnumConverter))]
        public D365RequiredLevel RequiredLevel { get; set; } = D365RequiredLevel.None;

        [JsonPropertyName("field_type")]
        [JsonConverter(typeof(JsonStringEnumConverter))]
        public D365FieldType FieldType { get; set; } = D365FieldType.String;

        [JsonPropertyName("searchable")]
        public bool Searchable { get; set; } = true;

        [JsonPropertyName("is_audit_enabled")]
        public bool IsAuditEnabled { get; set; } = false;

        // 各类型扩展配置（仅对应类型时生效）
        [JsonPropertyName("string")]
        public StringFieldConfig? String { get; set; }

        [JsonPropertyName("memo")]
        public MemoFieldConfig? Memo { get; set; }

        [JsonPropertyName("integer")]
        public IntegerFieldConfig? Integer { get; set; }

        [JsonPropertyName("decimal")]
        public DecimalFieldConfig? Decimal { get; set; }

        [JsonPropertyName("double")]
        public DoubleFieldConfig? Double { get; set; }

        [JsonPropertyName("money")]
        public MoneyFieldConfig? Money { get; set; }

        [JsonPropertyName("datetime")]
        public DateTimeFieldConfig? DateTime { get; set; }

        [JsonPropertyName("boolean")]
        public BooleanFieldConfig? Boolean { get; set; }

        [JsonPropertyName("picklist")]
        public PicklistFieldConfig? Picklist { get; set; }

        [JsonPropertyName("lookup")]
        public LookupFieldConfig? Lookup { get; set; }
    }

    public enum D365FieldType
    {
        String,
        Memo,
        Integer,
        Decimal,
        Double,
        Money,
        DateTime,
        Boolean,
        Picklist,
        Lookup
    }

    public enum D365RequiredLevel
    {
        None,
        Recommended,
        ApplicationRequired
    }

    public sealed class StringFieldConfig
    {
        [JsonPropertyName("max_length")]
        public int MaxLength { get; set; } = 100;

        [JsonPropertyName("format")]
        public string Format { get; set; } = "Text";
    }

    public sealed class MemoFieldConfig
    {
        [JsonPropertyName("max_length")]
        public int MaxLength { get; set; } = 2000;
    }

    public sealed class IntegerFieldConfig
    {
        [JsonPropertyName("min_value")]
        public int MinValue { get; set; } = int.MinValue;

        [JsonPropertyName("max_value")]
        public int MaxValue { get; set; } = int.MaxValue;
    }

    public sealed class DecimalFieldConfig
    {
        [JsonPropertyName("min_value")]
        public decimal MinValue { get; set; } = -100000000000m;

        [JsonPropertyName("max_value")]
        public decimal MaxValue { get; set; } = 100000000000m;

        [JsonPropertyName("precision")]
        public int Precision { get; set; } = 2;
    }

    public sealed class DoubleFieldConfig
    {
        [JsonPropertyName("min_value")]
        public double MinValue { get; set; } = -100000000000d;

        [JsonPropertyName("max_value")]
        public double MaxValue { get; set; } = 100000000000d;

        [JsonPropertyName("precision")]
        public int Precision { get; set; } = 2;
    }

    public sealed class MoneyFieldConfig
    {
        [JsonPropertyName("min_value")]
        public decimal MinValue { get; set; } = -922337203685477m;

        [JsonPropertyName("max_value")]
        public decimal MaxValue { get; set; } = 922337203685477m;

        [JsonPropertyName("precision")]
        public int Precision { get; set; } = 2;
    }

    public sealed class DateTimeFieldConfig
    {
        [JsonPropertyName("format")]
        public string Format { get; set; } = "DateOnly";

        [JsonPropertyName("behavior")]
        public string Behavior { get; set; } = "UserLocal";
    }

    public sealed class BooleanFieldConfig
    {
        [JsonPropertyName("true_label")]
        public string TrueLabel { get; set; } = "是";

        [JsonPropertyName("false_label")]
        public string FalseLabel { get; set; } = "否";

        [JsonPropertyName("default_value")]
        public bool DefaultValue { get; set; } = false;
    }

    public sealed class PicklistFieldConfig
    {
        /// <summary>
        /// local 或 global
        /// </summary>
        [JsonPropertyName("mode")]
        public string Mode { get; set; } = "local";

        [JsonPropertyName("global_option_set_name")]
        public string? GlobalOptionSetName { get; set; }

        [JsonPropertyName("default_value")]
        public int? DefaultValue { get; set; }

        [JsonPropertyName("options")]
        public List<PicklistOption> Options { get; set; } = new();
    }

    public sealed class PicklistOption
    {
        [JsonPropertyName("value")]
        public int Value { get; set; }

        [JsonPropertyName("label")]
        public string Label { get; set; } = string.Empty;
    }

    public sealed class LookupFieldConfig
    {
        [JsonPropertyName("target_entity")]
        public string TargetEntity { get; set; } = string.Empty;

        [JsonPropertyName("relationship_schema_name")]
        public string? RelationshipSchemaName { get; set; }
    }

    #endregion

    #region Validation

    public static class FieldSchemaValidator
    {
        public static void Validate(FieldImportSchema schema)
        {
            if (schema.Solutions == null || schema.Solutions.Count == 0)
            {
                throw new InvalidOperationException("At least one solution is required.");
            }

            foreach (var solution in schema.Solutions)
            {
                if (string.IsNullOrWhiteSpace(solution.SolutionUniqueName))
                {
                    throw new InvalidOperationException("solution_unique_name is required.");
                }

                foreach (var entity in solution.Entities)
                {
                    if (string.IsNullOrWhiteSpace(entity.EntityLogicalName))
                    {
                        throw new InvalidOperationException("entity_logical_name is required.");
                    }

                    var logicalNameSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    foreach (var field in entity.Fields)
                    {
                        if (!logicalNameSet.Add(field.LogicalName))
                        {
                            throw new InvalidOperationException($"Duplicate logical_name: {field.LogicalName}");
                        }

                        if (!string.IsNullOrWhiteSpace(schema.PublisherPrefix) &&
                            !field.LogicalName.StartsWith(schema.PublisherPrefix + "_", StringComparison.OrdinalIgnoreCase))
                        {
                            throw new InvalidOperationException(
                                $"Field {field.LogicalName} must start with {schema.PublisherPrefix}_");
                        }

                        ValidateTypeConfig(field);
                    }
                }
            }
        }

        private static void ValidateTypeConfig(FieldDefinition field)
        {
            switch (field.FieldType)
            {
                case D365FieldType.String:
                    field.String ??= new StringFieldConfig();
                    break;
                case D365FieldType.Memo:
                    field.Memo ??= new MemoFieldConfig();
                    break;
                case D365FieldType.Integer:
                    field.Integer ??= new IntegerFieldConfig();
                    break;
                case D365FieldType.Decimal:
                    field.Decimal ??= new DecimalFieldConfig();
                    break;
                case D365FieldType.Double:
                    field.Double ??= new DoubleFieldConfig();
                    break;
                case D365FieldType.Money:
                    field.Money ??= new MoneyFieldConfig();
                    break;
                case D365FieldType.DateTime:
                    field.DateTime ??= new DateTimeFieldConfig();
                    break;
                case D365FieldType.Boolean:
                    field.Boolean ??= new BooleanFieldConfig();
                    break;
                case D365FieldType.Picklist:
                    field.Picklist ??= new PicklistFieldConfig();
                    var mode = (field.Picklist.Mode ?? "local").Trim().ToLowerInvariant();
                    if (mode == "global")
                    {
                        if (string.IsNullOrWhiteSpace(field.Picklist.GlobalOptionSetName))
                        {
                            throw new InvalidOperationException(
                                $"picklist(global) requires global_option_set_name: {field.LogicalName}");
                        }
                    }
                    else
                    {
                        if (field.Picklist.Options.Count == 0)
                        {
                            throw new InvalidOperationException(
                                $"picklist(local) requires options: {field.LogicalName}");
                        }
                    }
                    break;
                case D365FieldType.Lookup:
                    field.Lookup ??= new LookupFieldConfig();
                    if (string.IsNullOrWhiteSpace(field.Lookup.TargetEntity))
                    {
                        throw new InvalidOperationException($"lookup target_entity is required: {field.LogicalName}");
                    }
                    break;
                default:
                    throw new InvalidOperationException($"Unsupported field type: {field.FieldType}");
            }
        }
    }

    #endregion

    #region Importer (TODO: Replace with your SDK/API calls)

    public sealed class D365FieldImporter
    {
        public void Import(FieldImportSchema schema)
        {
            foreach (var solution in schema.Solutions)
            {
                foreach (var entity in solution.Entities)
                {
                    foreach (var field in entity.Fields)
                    {
                        if (FieldExists(entity.EntityLogicalName, field.LogicalName))
                        {
                            Console.WriteLine($"Skip existing field: {entity.EntityLogicalName}.{field.LogicalName}");
                            continue;
                        }

                        var payload = BuildFieldPayload(field);
                        CreateField(entity.EntityLogicalName, solution.SolutionUniqueName, payload);
                        Console.WriteLine(
                            $"Created field: {entity.EntityLogicalName}.{field.LogicalName}, type={field.FieldType}");
                    }
                }
            }
        }

        private bool FieldExists(string entityLogicalName, string fieldLogicalName)
        {
            // TODO: 用你的 D365 SDK 或 Web API 检查字段是否已存在
            // 示例：RetrieveAttributeRequest / EntityDefinitions(...)/Attributes(...)
            return false;
        }

        private Dictionary<string, object?> BuildFieldPayload(FieldDefinition field)
        {
            var common = new Dictionary<string, object?>
            {
                ["LogicalName"] = field.LogicalName,
                ["SchemaName"] = field.SchemaName,
                ["DisplayName"] = BuildLocalizedLabel(field.DisplayName),
                ["Description"] = BuildLocalizedLabel(field.Description),
                ["RequiredLevel"] = new Dictionary<string, object?> { ["Value"] = field.RequiredLevel.ToString() },
                ["IsAuditEnabled"] = new Dictionary<string, object?> { ["Value"] = field.IsAuditEnabled },
                ["IsValidForAdvancedFind"] = new Dictionary<string, object?> { ["Value"] = field.Searchable }
            };

            switch (field.FieldType)
            {
                case D365FieldType.String:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.StringAttributeMetadata";
                    common["MaxLength"] = field.String!.MaxLength;
                    common["FormatName"] = new Dictionary<string, object?> { ["Value"] = field.String!.Format };
                    break;
                case D365FieldType.Memo:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.MemoAttributeMetadata";
                    common["MaxLength"] = field.Memo!.MaxLength;
                    break;
                case D365FieldType.Integer:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.IntegerAttributeMetadata";
                    common["MinValue"] = field.Integer!.MinValue;
                    common["MaxValue"] = field.Integer!.MaxValue;
                    break;
                case D365FieldType.Decimal:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.DecimalAttributeMetadata";
                    common["MinValue"] = field.Decimal!.MinValue;
                    common["MaxValue"] = field.Decimal!.MaxValue;
                    common["Precision"] = field.Decimal!.Precision;
                    break;
                case D365FieldType.Double:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.DoubleAttributeMetadata";
                    common["MinValue"] = field.Double!.MinValue;
                    common["MaxValue"] = field.Double!.MaxValue;
                    common["Precision"] = field.Double!.Precision;
                    break;
                case D365FieldType.Money:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.MoneyAttributeMetadata";
                    common["MinValue"] = field.Money!.MinValue;
                    common["MaxValue"] = field.Money!.MaxValue;
                    common["Precision"] = field.Money!.Precision;
                    break;
                case D365FieldType.DateTime:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata";
                    common["Format"] = field.DateTime!.Format;
                    common["DateTimeBehavior"] = new Dictionary<string, object?> { ["Value"] = field.DateTime!.Behavior };
                    break;
                case D365FieldType.Boolean:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.BooleanAttributeMetadata";
                    common["OptionSet"] = new Dictionary<string, object?>
                    {
                        ["FalseOption"] = new Dictionary<string, object?>
                        {
                            ["Value"] = 0,
                            ["Label"] = BuildLocalizedLabel(field.Boolean!.FalseLabel)
                        },
                        ["TrueOption"] = new Dictionary<string, object?>
                        {
                            ["Value"] = 1,
                            ["Label"] = BuildLocalizedLabel(field.Boolean!.TrueLabel)
                        }
                    };
                    common["DefaultValue"] = field.Boolean!.DefaultValue;
                    break;
                case D365FieldType.Picklist:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.PicklistAttributeMetadata";
                    var mode = (field.Picklist!.Mode ?? "local").Trim().ToLowerInvariant();
                    if (mode == "global")
                    {
                        common["GlobalOptionSet@odata.bind"] =
                            $"/GlobalOptionSetDefinitions(Name='{field.Picklist!.GlobalOptionSetName}')";
                    }
                    else
                    {
                        common["OptionSet"] = new Dictionary<string, object?>
                        {
                            ["@odata.type"] = "Microsoft.Dynamics.CRM.OptionSetMetadata",
                            ["OptionSetType"] = "Picklist",
                            ["IsGlobal"] = false,
                            ["Options"] = field.Picklist.Options.Select(x => new Dictionary<string, object?>
                            {
                                ["Value"] = x.Value,
                                ["Label"] = BuildLocalizedLabel(x.Label)
                            }).ToList()
                        };
                    }
                    if (field.Picklist.DefaultValue.HasValue)
                    {
                        common["DefaultFormValue"] = field.Picklist.DefaultValue.Value;
                    }
                    break;
                case D365FieldType.Lookup:
                    common["@odata.type"] = "Microsoft.Dynamics.CRM.LookupAttributeMetadata";
                    common["Targets"] = new[] { field.Lookup!.TargetEntity };
                    if (!string.IsNullOrWhiteSpace(field.Lookup!.RelationshipSchemaName))
                    {
                        common["RelationshipSchemaName"] = field.Lookup!.RelationshipSchemaName;
                    }
                    break;
                default:
                    throw new InvalidOperationException($"Unsupported field type: {field.FieldType}");
            }

            return common;
        }

        private Dictionary<string, object?> BuildLocalizedLabel(string text)
        {
            return new Dictionary<string, object?>
            {
                ["LocalizedLabels"] = new List<Dictionary<string, object?>>
                {
                    new()
                    {
                        ["Label"] = text ?? string.Empty,
                        ["LanguageCode"] = 2052
                    }
                }
            };
        }

        private void CreateField(string entityLogicalName, string solutionUniqueName, Dictionary<string, object?> payload)
        {
            // TODO: 用你的 D365 SDK 或 Web API 真正创建字段
            // WebAPI 例子：POST EntityDefinitions(LogicalName='entity')/Attributes?MSCRM.SolutionUniqueName=xxx
            // body = payload
        }
    }

    #endregion

    #region Sample JSON

    /*
    {
      "publisher_prefix": "mcs",
      "solutions": [
        {
          "solution_unique_name": "entity_contract_ext",
          "entities": [
            {
              "entity_logical_name": "mcs_contract_ext",
              "fields": [
                {
                  "logical_name": "mcs_tradingplatform",
                  "schema_name": "mcs_tradingplatform",
                  "display_name": "交易平台",
                  "description": "交易平台",
                  "required_level": "None",
                  "field_type": "String",
                  "string": {
                    "max_length": 100,
                    "format": "Text"
                  }
                },
                {
                  "logical_name": "mcs_sign_status",
                  "schema_name": "mcs_sign_status",
                  "display_name": "签署状态",
                  "field_type": "Picklist",
                  "picklist": {
                    "mode": "local",
                    "default_value": 100000000,
                    "options": [
                      { "value": 100000000, "label": "待签" },
                      { "value": 100000001, "label": "已签" }
                    ]
                  }
                },
                {
                  "logical_name": "mcs_customerid",
                  "schema_name": "mcs_customerid",
                  "display_name": "客户",
                  "field_type": "Lookup",
                  "lookup": {
                    "target_entity": "account",
                    "relationship_schema_name": "mcs_account_contract"
                  }
                }
              ]
            }
          ]
        }
      ]
    }
    */

    #endregion
}
