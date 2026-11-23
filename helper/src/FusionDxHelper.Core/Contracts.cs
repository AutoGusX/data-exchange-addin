using System.Text.Json.Serialization;

namespace FusionDxHelper.Core;

/// <summary>Structured error codes returned by the helper (contracts/errors.schema.json).</summary>
public static class ErrorCodes
{
    public const string AuthRequired = "AUTH_REQUIRED";
    public const string Forbidden = "FORBIDDEN";
    public const string NotFound = "NOT_FOUND";
    public const string SdkUnavailable = "SDK_UNAVAILABLE";
    public const string UnsupportedGeometry = "UNSUPPORTED_GEOMETRY";
    public const string NetworkError = "NETWORK_ERROR";
    public const string InvalidRequest = "INVALID_REQUEST";
    public const string NotConfigured = "NOT_CONFIGURED";
    public const string InternalError = "INTERNAL_ERROR";
    /// <summary>An AEC Data Model based exchange: data only, no geometry, not readable through the SDK.</summary>
    public const string DataOnlyExchange = "DATA_ONLY_EXCHANGE";
}

/// <summary>Recognises the service's refusal to read an AEC Data Model based exchange.</summary>
public static class DataOnlyExchange
{
    public const string Message =
        "This is an AEC Data Model exchange: it holds data only, with no geometry, so there is nothing to load.";

    /// <summary>True when any of the error texts is the service saying the exchange is AECDM based
    /// (seen live 2026-11-17: "The request is invalid: This API does not support AECDM based exchange.").</summary>
    public static bool Matches(IEnumerable<string?> texts) =>
        texts.Any(text => text != null
            && (text.Contains("AECDM", StringComparison.OrdinalIgnoreCase)
                || text.Contains("AEC Data Model", StringComparison.OrdinalIgnoreCase)));
}

public sealed class HelperException(string code, string message, Exception? inner = null)
    : Exception(message, inner)
{
    public string Code { get; } = code;
}

public sealed record ErrorBody(string Code, string Message, string CorrelationId);

public sealed record AccountInfo(string UserId, string DisplayName, string Email, string? Scopes = null);

public sealed record HubDto(string HubId, string Name, string? Region);

public sealed record ProjectDto(string ProjectId, string Name, string HubId, string? ProjectType);

public sealed record FolderDto(string FolderId, string Name, bool HasChildren, bool ReadOnly);

/// <summary>One element of a data-only (AEC Data Model based) exchange: name, category, properties.</summary>
public sealed record DataElement(string Name, string? Category, Dictionary<string, string>? Properties)
{
    public const int MaxProperties = 40;
    public const int MaxValueLength = 200;
}

/// <summary>The element data of a data-only exchange. Total is the real count; Listed is capped.</summary>
public sealed record DataOnlyElements(string? Name, int Total, IReadOnlyList<DataElement> Listed, string Source)
{
    public const int MaxListed = 200;
}

/// <summary>An exchange as listed in a folder, before it has been resolved to an <see cref="ExchangeRef"/>.</summary>
public sealed record ExchangeListItem(string FileUrn, string Name);

/// <summary>
/// Everything needed to address one exchange again later. All ids are opaque strings; the add-in
/// stores this in the Fusion document and sends it back unchanged.
/// </summary>
public sealed record ExchangeRef(
    string ExchangeId,
    string CollectionId,
    string HubId,
    string? ProjectId = null,
    string? FileUrn = null,
    string? Name = null,
    string? ProjectName = null,
    string? FolderUrn = null,
    // Region of the hub (US, EMEA, AUS, ...): decides the web address of the exchange in Autodesk Forma.
    string? Region = null);

public sealed record RevisionInfo(string RevisionId, DateTime? LastModifiedUtc, int RevisionCount);

/// <summary>One element that differs between two revisions.</summary>
public sealed record ElementChange(string SourceElementId, string? Name, string? Category);

/// <summary>
/// What changed in an exchange after <see cref="FromRevisionId"/>. Counts are complete; the lists hold
/// at most <see cref="MaxListed"/> entries each. <see cref="BaseFound"/> is false when the starting
/// revision is not in the exchange's history (the counts then cover the whole history).
/// </summary>
public sealed record RevisionChanges(
    string FromRevisionId,
    string ToRevisionId,
    int RevisionsBehind,
    bool BaseFound,
    int CreatedCount,
    int ModifiedCount,
    int DeletedCount,
    IReadOnlyList<ElementChange> Created,
    IReadOnlyList<ElementChange> Modified,
    IReadOnlyList<ElementChange> Deleted)
{
    public const int MaxListed = 25;
}

/// <summary>The SDK's latest progress report for the transfer in flight (export or publish).</summary>
public sealed record ProgressInfo(string StepId, double Percent, DateTime UpdatedUtc);

/// <summary>A watched exchange got a new revision (pushed by the service, no polling of the cloud).</summary>
public sealed record ExchangeUpdate(string ExchangeId, string? RevisionId, DateTime SeenUtc);

public sealed record StatusInfo(ProgressInfo? Progress, IReadOnlyList<ExchangeUpdate> Updates, int Watching);

public sealed record ManifestGeometry(string Kind, string? BlobRef);

public sealed record ManifestElement(
    string SourceElementId,
    string? UniqueId,
    string? DisplayName,
    string? Category,
    bool HasGeometry,
    double[]? Transform,
    ManifestGeometry? Geometry,
    Dictionary<string, string>? Properties);

public sealed record ManifestWarning(string Code, string Message, string? SourceElementId = null);

/// <summary>Normalized export handed to the add-in (contracts/manifest.schema.json, schemaVersion 1).</summary>
public sealed record Manifest
{
    public int SchemaVersion { get; init; } = 1;
    public required string ExchangeId { get; init; }
    public required string CollectionId { get; init; }
    public required string HubId { get; init; }
    public required string RevisionId { get; init; }
    public string? ExchangeName { get; init; }
    public string? SourceApplication { get; init; }
    /// <summary>Units of the whole-exchange geometry file; null when the file carries its own (STEP does).</summary>
    public string? Units { get; init; }
    public string? CoordinateSystem { get; init; }
    /// <summary>The length unit the source elements report (as text from the SDK), when known.</summary>
    public string? SourceLengthUnit { get; init; }
    /// <summary>Whole-exchange geometry file, relative to the manifest's folder.</summary>
    public ManifestGeometry? ExchangeGeometry { get; init; }
    public List<ManifestElement> Elements { get; init; } = [];
    public List<ManifestWarning> Warnings { get; init; } = [];
    /// <summary>"sdk" for a real cloud exchange, "mock" for fixture data. Never silently mixed.</summary>
    public required string Source { get; init; }
    public DateTime ExportedAtUtc { get; init; } = DateTime.UtcNow;
}

public sealed record ExportResult(string ManifestPath, string OutputDirectory, Manifest Manifest);

public sealed record NewExchangeTarget(string HubId, string ProjectId, string FolderId, string Name);

/// <summary>Publish one STEP file as the geometry of one element. Exactly one of Existing / New is set.</summary>
public sealed record PublishRequest
{
    public ExchangeRef? Existing { get; init; }
    public NewExchangeTarget? New { get; init; }
    public required string StepPath { get; init; }
    public required string ElementSourceId { get; init; }
    public required string ElementName { get; init; }
    /// <summary>Name and value pairs published as custom parameters of the first element.</summary>
    public Dictionary<string, string>? Properties { get; init; }
    /// <summary>Further elements of the same publish, each with its own STEP file and properties.</summary>
    public IReadOnlyList<PublishElement>? MoreElements { get; init; }
    /// <summary>
    /// On an update, also remove elements this add-in published before that are not in this request
    /// (source ids starting with <see cref="OwnedPrefix"/>). Other authors' elements are never touched.
    /// </summary>
    public bool ReplaceOwned { get; init; }

    public const string OwnedPrefix = "fusion:";

    public IReadOnlyList<PublishElement> AllElements() =>
        [new PublishElement(ElementSourceId, ElementName, StepPath, Properties), .. MoreElements ?? []];
    /// <summary>"Y" or "Z": which model axis is up, so the web viewer shows the design upright. Optional.</summary>
    public string? UpAxis { get; init; }
    public string? Category { get; init; }
}

public sealed record PublishElement(string SourceId, string Name, string StepPath, Dictionary<string, string>? Properties = null);

public sealed record PublishResult(
    ExchangeRef Exchange, RevisionInfo Revision, bool Created, int ElementCount = 1, int PropertyCount = 0,
    IReadOnlyList<string>? Warnings = null);

public sealed record HealthInfo(
    string HelperVersion,
    string Mode,
    string? SdkVersion,
    bool Ready,
    bool Configured,
    bool SignedIn,
    string? CallbackUrl);

[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase, WriteIndented = true)]
[JsonSerializable(typeof(Manifest))]
public partial class ManifestJsonContext : JsonSerializerContext;
