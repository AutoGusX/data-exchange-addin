namespace FusionDxHelper.Core;

/// <summary>
/// The only seam between the helper's HTTP surface and Autodesk. The real implementation lives in
/// FusionDxHelper.AutodeskSdk; <see cref="MockExchangeService"/> serves fixtures for tests and for
/// add-in work before live access exists.
/// </summary>
public interface IExchangeService
{
    /// <summary>"sdk" or "mock".</summary>
    string Mode { get; }
    string? SdkVersion { get; }
    bool Configured { get; }
    string? CallbackUrl { get; }
    AccountInfo? CurrentAccount { get; }

    Task<AccountInfo> LoginAsync(CancellationToken ct);
    Task LogoutAsync(CancellationToken ct);

    Task<IReadOnlyList<HubDto>> ListHubsAsync(CancellationToken ct);
    Task<IReadOnlyList<ProjectDto>> ListProjectsAsync(string hubId, CancellationToken ct);
    /// <summary>Top folders of the project when <paramref name="parentFolderId"/> is null.</summary>
    Task<IReadOnlyList<FolderDto>> ListFoldersAsync(string hubId, string projectId, string? parentFolderId, CancellationToken ct);
    Task<IReadOnlyList<ExchangeListItem>> ListExchangesAsync(string hubId, string projectId, string folderId, CancellationToken ct);
    Task<ExchangeRef> ResolveExchangeAsync(string hubId, string projectId, string fileUrn, CancellationToken ct);

    Task<RevisionInfo> GetLatestRevisionAsync(ExchangeRef exchange, CancellationToken ct);
    /// <summary>Writes manifest.json plus geometry into <paramref name="outputDirectory"/>.</summary>
    /// <summary>Elements created, modified and deleted after the given revision, up to the latest.</summary>
    Task<RevisionChanges> GetChangesAsync(ExchangeRef exchange, string fromRevisionId, CancellationToken ct);
    Task<ExportResult> ExportAsync(ExchangeRef exchange, string outputDirectory, CancellationToken ct);
    /// <summary>
    /// Same as <see cref="ExportAsync"/> but the geometry is one mesh (OBJ) file. For exchanges whose
    /// geometry does not survive as solids in STEP.
    /// </summary>
    Task<ExportResult> ExportMeshAsync(ExchangeRef exchange, string outputDirectory, CancellationToken ct);
    Task<PublishResult> PublishAsync(PublishRequest request, CancellationToken ct);

    /// <summary>PNG preview of an exchange as listed in a folder, or null when it has none.</summary>
    /// <summary>Cheap, local: transfer progress and updates seen for watched exchanges. Never calls the cloud.</summary>
    StatusInfo GetStatus();
    /// <summary>Ask the service to tell us when these exchanges get a new revision. Returns how many are watched.</summary>
    Task<int> WatchAsync(IReadOnlyList<string> exchangeIds, CancellationToken ct);

    /// <summary>Permanently deletes an exchange from its folder. The caller confirms with the user first.</summary>
    Task DeleteExchangeAsync(string hubId, string projectId, string fileUrn, CancellationToken ct);

    /// <summary>Element data of a data-only (AEC Data Model based) exchange, read through the
    /// AEC Data Model API. Read-only.</summary>
    Task<DataOnlyElements> GetDataOnlyElementsAsync(string hubId, string projectId, string fileUrn, CancellationToken ct);

    Task<byte[]?> GetThumbnailAsync(string hubId, string projectId, string fileUrn, CancellationToken ct);
}
