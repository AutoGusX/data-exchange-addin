using System.Collections.Concurrent;
using Autodesk.DataExchange;
using Autodesk.DataExchange.Authentication;
using Autodesk.DataExchange.Core.Enums;
using Autodesk.DataExchange.Core.Interface;
using Autodesk.DataExchange.Core.Models;
using Autodesk.DataExchange.DataModels;
using Autodesk.DataExchange.Interface;
using Autodesk.DataExchange.Models;
using Autodesk.DataExchange.SchemaObjects.Units;
using Autodesk.Parameters;
using FusionDxHelper.Core;

namespace FusionDxHelper.AutodeskSdk;

public sealed record SdkSettings(string? ClientId, string CallbackUrl, string LogDirectory)
{
    /// <summary>Default OAuth callback. Must be registered on the APS app exactly, trailing slash included.</summary>
    public const string DefaultCallbackUrl = "http://localhost:47821/";
}

/// <summary>
/// The only file that calls the Autodesk Data Exchange SDK (Autodesk.DataExchange 8.1.0).
/// Every call here follows Autodesk's aps-dataexchange-console sample or the SDK's XML docs;
/// docs/feasibility.md records which calls have been proven against a live exchange.
/// Auth is PKCE: no client secret is ever supplied (SDK: "Supplying ClientSecret selects the
/// three-legged flow; without it, PKCE is used").
/// </summary>
public sealed class SdkExchangeService(SdkSettings settings) : IExchangeService
{
    private readonly SemaphoreSlim loginGate = new(1, 1);
    private readonly ConcurrentDictionary<string, HubInfo> hubs = new();
    private readonly ConcurrentDictionary<string, ProjectInfo> projects = new();
    private readonly ConcurrentDictionary<string, FolderInfo> folders = new();
    /// <summary>PNG bytes per exchange file URN; empty array = known to have no thumbnail.</summary>
    private readonly ConcurrentDictionary<string, byte[]> thumbnails = new();
    private readonly ConcurrentDictionary<string, ExchangeUpdate> updates = new();
    private readonly ConcurrentDictionary<string, bool> watched = new();
    private readonly object eventsGate = new();
    private bool eventsHooked;
    private volatile ProgressInfo? progress;
    private Client? client;
    private Auth? signedIn;
    /// <summary>The element data last read, so a refresh (compare, then download) reads it once.</summary>
    private (string ExchangeId, string RevisionId, Autodesk.DataExchange.Interface.IElementDataModel Model, DateTime At)? lastModel;
    private static readonly TimeSpan ModelLife = TimeSpan.FromMinutes(3);
    private AccountInfo? account;

    public string Mode => "sdk";
    public string? SdkVersion => typeof(Client).Assembly.GetName().Version?.ToString(3);
    public bool Configured => !string.IsNullOrWhiteSpace(settings.ClientId);
    public string? CallbackUrl => settings.CallbackUrl;
    public AccountInfo? CurrentAccount => account;

    public async Task<AccountInfo> LoginAsync(CancellationToken ct)
    {
        if (!Configured)
            throw new HelperException(ErrorCodes.NotConfigured,
                "No APS client ID configured. Set DX_CLIENT_ID or add clientId to the helper config file.");

        await loginGate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (account != null)
                return account;

            Directory.CreateDirectory(settings.LogDirectory);
            var logger = new Autodesk.DataExchange.Extensions.Logging.File.Log(settings.LogDirectory);
            var auth = new Auth(new AuthOptions
            {
                ClientId = settings.ClientId!.Trim(),
                CallBack = settings.CallbackUrl,
                Logger = logger,
            });

            var created = await Run(() => Task.Run(() => new Client(new SDKOptionsDefaultSetup
            {
                ConnectorName = "FusionDataExchange",
                ConnectorVersion = HelperVersion.Value,
                HostApplicationName = "Fusion",
                HostApplicationVersion = HelperVersion.Value,
                AuthProvider = auth,
                Logger = logger,
            }), ct)).ConfigureAwait(false);

            // Opens the system browser and waits for the redirect on the callback port.
            var token = await Run(() => auth.GetAuthTokenAsync()).ConfigureAwait(false);
            var scopes = ReadScopes(token);

            // The profile lookup is cosmetic: observed returning 403 while the token itself was
            // valid (docs/feasibility.md). Sign-in must not fail because a name is unavailable.
            try
            {
                var user = await auth.GetUserAccountAsync().ConfigureAwait(false);
                account = new AccountInfo(
                    user.UserId ?? string.Empty,
                    $"{user.FirstName} {user.LastName}".Trim(),
                    user.Email ?? string.Empty,
                    scopes);
            }
            catch (Exception)
            {
                account = new AccountInfo(string.Empty, "your Autodesk account", "profile not available", scopes);
            }

            // Progress is relayed to the add-in's job line. A listener problem must never block sign-in.
            try
            {
                created.ProgressStepsManager.RegisterProgressUpdateListener(new ProgressRelay(this));
            }
            catch (Exception)
            {
            }

            signedIn = auth;
            client = created;
            return account;
        }
        finally
        {
            loginGate.Release();
        }
    }

    public Task LogoutAsync(CancellationToken ct)
    {
        // The SDK exposes no sign-out call. Dropping the client forgets the in-memory session; whether
        // the SDK also keeps a token cache on disk is an open item in docs/feasibility.md.
        client = null;
        signedIn = null;
        lastModel = null;
        account = null;
        updates.Clear();
        watched.Clear();
        progress = null;
        lock (eventsGate)
            eventsHooked = false;
        hubs.Clear();
        projects.Clear();
        folders.Clear();
        thumbnails.Clear();
        return Task.CompletedTask;
    }

    public async Task<IReadOnlyList<HubDto>> ListHubsAsync(CancellationToken ct)
    {
        var hosting = Hosting();
        var result = await Run(() => hosting.GetHubsAsync()).ConfigureAwait(false);
        var list = new List<HubDto>();
        foreach (var hub in result ?? [])
        {
            hubs[hub.HubId] = hub;
            list.Add(new HubDto(hub.HubId, hub.HubName, hub.HubRegion));
        }
        return list;
    }

    public async Task<IReadOnlyList<ProjectDto>> ListProjectsAsync(string hubId, CancellationToken ct)
    {
        var hosting = Hosting();
        if (!hubs.TryGetValue(hubId, out var hub))
        {
            hub = await Run(() => hosting.GetHubAsync(hubId)).ConfigureAwait(false)
                ?? throw new HelperException(ErrorCodes.NotFound, "Hub not found.");
            hubs[hubId] = hub;
        }

        var result = await Run(() => hosting.GetProjectsAsync(hub)).ConfigureAwait(false);
        var list = new List<ProjectDto>();
        foreach (var project in result ?? [])
        {
            projects[project.ProjectId] = project;
            list.Add(new ProjectDto(project.ProjectId, project.ProjectName, hubId, project.ProjectType.ToString()));
        }
        return list;
    }

    public async Task<IReadOnlyList<FolderDto>> ListFoldersAsync(string hubId, string projectId, string? parentFolderId, CancellationToken ct)
    {
        var hosting = Hosting();
        IEnumerable<FolderInfo>? result;
        if (parentFolderId == null)
        {
            var project = await ProjectAsync(hubId, projectId).ConfigureAwait(false);
            result = await Run(() => hosting.GetProjectFoldersAsync(project)).ConfigureAwait(false);
        }
        else
        {
            if (!folders.TryGetValue(parentFolderId, out var parent))
                throw new HelperException(ErrorCodes.NotFound, "Folder not listed yet; browse from the project root.");
            result = await Run(() => hosting.GetChildrenFoldersAsync(parent)).ConfigureAwait(false);
        }

        var list = new List<FolderDto>();
        foreach (var folder in result ?? [])
        {
            folders[folder.FolderId] = folder;
            list.Add(new FolderDto(folder.FolderId, folder.FolderName, folder.IsChildFolderPresent, folder.IsReadOnly));
        }
        return list;
    }

    public async Task<IReadOnlyList<ExchangeListItem>> ListExchangesAsync(string hubId, string projectId, string folderId, CancellationToken ct)
    {
        var hosting = Hosting();
        var project = await ProjectAsync(hubId, projectId).ConfigureAwait(false);
        var urns = new List<string>();
        for (var page = 0; ; page++)
        {
            var current = page;
            var result = await Run(() => hosting.GetAllExchangesInfoAsync(project.ProjectId, folderId, current, false))
                .ConfigureAwait(false);
            foreach (var pair in result.ExchangeIds)
                urns.Add(pair.Item1);
            if (!result.Next)
                break;
        }

        var items = await Task.WhenAll(urns.Select(async urn =>
        {
            string? name = null;
            try
            {
                var descriptor = await hosting.GetExchangeMetadataDescriptorAsync(project.ProjectId, urn).ConfigureAwait(false);
                name = descriptor?.Metadata?.Data?.Attributes?.DisplayName?.Trim();
                // The metadata read brings the thumbnail with it, so keep it for the preview.
                Remember(urn, descriptor?.Metadata?.ThumbnailsImage);
            }
            catch (Exception)
            {
                // A missing display name is not worth failing the listing for.
            }
            return new ExchangeListItem(urn, string.IsNullOrWhiteSpace(name) ? urn : name);
        })).ConfigureAwait(false);
        return items;
    }

    public async Task<ExchangeRef> ResolveExchangeAsync(string hubId, string projectId, string fileUrn, CancellationToken ct)
    {
        var sdk = Sdk();
        var project = await ProjectAsync(hubId, projectId).ConfigureAwait(false);
        var collectionId = Value(await Run(() => sdk.GetCollectionIdAsync(project.ProjectId, hubId)).ConfigureAwait(false),
            "collection id");
        var details = Value(await Run(() => sdk.GetExchangeDetailsAsync(collectionId, fileUrn)).ConfigureAwait(false),
            "exchange details");
        return ToRef(details, hubId, project.ProjectId, project.HubRegion);
    }

    public async Task<RevisionInfo> GetLatestRevisionAsync(ExchangeRef exchange, CancellationToken ct)
    {
        var sdk = Sdk();
        var identifier = Identifier(exchange);
        var revisions = Value(await Run(() => sdk.GetExchangeRevisionsAsync(identifier)).ConfigureAwait(false), "revisions")
            .OrderByDescending(r => r.LastModifiedUTC)
            .ToList();
        if (revisions.Count == 0)
            throw new HelperException(ErrorCodes.NotFound, "The exchange has no published revision yet.");
        return new RevisionInfo(revisions[0].Id, revisions[0].LastModifiedUTC, revisions.Count);
    }

    public async Task<DataOnlyElements> GetDataOnlyElementsAsync(string hubId, string projectId, string fileUrn, CancellationToken ct)
    {
        Sdk();
        var auth = signedIn ?? throw new HelperException(ErrorCodes.AuthRequired, "Sign in first.");
        var project = await ProjectAsync(hubId, projectId).ConfigureAwait(false);
        // The token stays inside the helper: it is used for this call and never returned or logged.
        var token = await Run(() => auth.GetAuthTokenAsync()).ConfigureAwait(false);
        return await AecDataModelReader.ReadAsync(token, project.HubRegion, hubId, project.ProjectId, fileUrn,
            DataOnlyElements.MaxListed, ct).ConfigureAwait(false);
    }

    public async Task DeleteExchangeAsync(string hubId, string projectId, string fileUrn, CancellationToken ct)
    {
        var sdk = Sdk();
        var project = await ProjectAsync(hubId, projectId).ConfigureAwait(false);
        var response = await Run(() => sdk.DeleteExchangeAsync(project.ProjectId, fileUrn)).ConfigureAwait(false);
        if (response.IsFailed)
            throw Classify(FirstError(response), "Could not delete the exchange");
        thumbnails.TryRemove(fileUrn, out _);
    }

    public StatusInfo GetStatus() => new(progress, updates.Values.ToList(), watched.Count);

    public Task<int> WatchAsync(IReadOnlyList<string> exchangeIds, CancellationToken ct)
    {
        var sdk = Sdk();
        var controller = sdk.EventsController;
        if (controller == null)
            return Task.FromResult(0);
        lock (eventsGate)
        {
            if (!eventsHooked)
            {
                controller.ExchangeModified += (_, e) =>
                {
                    if (!string.IsNullOrWhiteSpace(e.ExchangeID))
                        updates[e.ExchangeID] = new ExchangeUpdate(e.ExchangeID, e.RevisionID, DateTime.UtcNow);
                };
                eventsHooked = true;
            }
        }
        foreach (var id in exchangeIds.Where(id => !string.IsNullOrWhiteSpace(id)))
        {
            if (!watched.TryAdd(id, true))
                continue;
            try
            {
                controller.SubscribeToExchangeUpdateEvent(id);
            }
            catch (Exception)
            {
                watched.TryRemove(id, out _);  // live notices are a convenience; a manual check still works
            }
        }
        return Task.FromResult(watched.Count);
    }

    /// <summary>
    /// Tells the web viewer which way is up, so a Y-up Fusion design is not shown lying on its side.
    /// Same call as Autodesk's console sample (which sets Z up, Y front, X north). Best effort.
    /// </summary>
    private static void SetUpAxis(ElementDataModel model, string? upAxis)
    {
        static Autodesk.GeometryUtilities.PrimitivesAPI.Vector3d V(double x, double y, double z) =>
            new() { X = x, Y = y, Z = z };
        try
        {
            if (string.Equals(upAxis, "Z", StringComparison.OrdinalIgnoreCase))
                model.SetViewableWorldCoordinates(new ViewableWorldCoordinates { UP = V(0, 0, 1), Front = V(0, 1, 0), North = V(1, 0, 0) });
            else if (string.Equals(upAxis, "Y", StringComparison.OrdinalIgnoreCase))
                model.SetViewableWorldCoordinates(new ViewableWorldCoordinates { UP = V(0, 1, 0), Front = V(0, 0, 1), North = V(1, 0, 0) });
        }
        catch (Exception)
        {
            // Orientation only affects how the viewer shows the model; the publish itself goes ahead.
        }
    }

    private void BeginTransfer(Client sdk, Autodesk.DataExchange.ProgressManager.Enums.Operation operation)
    {
        progress = null;
        try
        {
            sdk.ProgressStepsManager.SetActiveOperation(operation);
        }
        catch (Exception)
        {
        }
    }

    private sealed class ProgressRelay(SdkExchangeService owner)
        : Autodesk.DataExchange.ProgressManager.Interfaces.IProgressUpdateListener
    {
        public void OnProgressUpdate(string stepId, double progress) =>
            owner.progress = new ProgressInfo(stepId ?? string.Empty, progress, DateTime.UtcNow);
    }

    public async Task<RevisionChanges> GetChangesAsync(ExchangeRef exchange, string fromRevisionId, CancellationToken ct)
    {
        var sdk = Sdk();
        var identifier = Identifier(exchange);
        var revisions = Value(await Run(() => sdk.GetExchangeRevisionsAsync(identifier)).ConfigureAwait(false), "revisions")
            .OrderBy(r => r.LastModifiedUTC)
            .Select(r => r.Id)
            .Where(id => !string.IsNullOrWhiteSpace(id))
            .ToList();
        if (revisions.Count == 0)
            throw new HelperException(ErrorCodes.NotFound, "The exchange has no published revision yet.");

        var index = revisions.IndexOf(fromRevisionId);
        var newer = index >= 0 ? revisions.Skip(index + 1).ToList() : revisions;
        var latest = revisions[^1];
        if (newer.Count == 0)
            return new RevisionChanges(fromRevisionId, latest, 0, true, 0, 0, 0, [], [], []);

        // The SDK reports changes per revision on a loaded model (same calls as Autodesk's console sample).
        var model = Value(await Run(() => sdk.GetElementDataModelAsync(identifier)).ConfigureAwait(false), "element data model");
        lastModel = (exchange.ExchangeId, latest, model, DateTime.UtcNow);
        var created = Changes(model.GetCreatedElements(newer));
        var modified = Changes(model.GetModifiedElements(newer));
        var deleted = Changes(model.GetDeletedElements(newer));
        return new RevisionChanges(fromRevisionId, latest, newer.Count, index >= 0,
            created.Count, modified.Count, deleted.Count,
            created.Take(RevisionChanges.MaxListed).ToList(),
            modified.Take(RevisionChanges.MaxListed).ToList(),
            deleted.Take(RevisionChanges.MaxListed).ToList());

        static List<ElementChange> Changes(IEnumerable<Autodesk.DataExchange.Interface.IElement>? elements) =>
            (elements ?? [])
                .Select(e => new ElementChange(
                    string.IsNullOrWhiteSpace(e.SourceId) ? e.UniqueId : e.SourceId, e.Name, e.Category))
                .ToList();
    }

    public Task<ExportResult> ExportAsync(ExchangeRef exchange, string outputDirectory, CancellationToken ct) =>
        ExportCoreAsync(exchange, outputDirectory, mesh: false, ct);

    public Task<ExportResult> ExportMeshAsync(ExchangeRef exchange, string outputDirectory, CancellationToken ct) =>
        ExportCoreAsync(exchange, outputDirectory, mesh: true, ct);

    private const int MaxPropertiesPerElement = 40;
    private const int MaxPropertyLength = 200;

    /// <summary>Category plus the element's instance parameters as text. Reading them is best effort.</summary>
    private static Dictionary<string, string>? ReadProperties(Autodesk.DataExchange.Interface.IElement element, string? category)
    {
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        if (!string.IsNullOrWhiteSpace(category))
            result["sourceCategory"] = category;
        try
        {
            foreach (var parameter in element.InstanceParameters ?? [])
            {
                if (result.Count >= MaxPropertiesPerElement)
                    break;
                var name = parameter?.Name;
                var value = parameter?.Value?.ToString();
                if (string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(value))
                    continue;
                result.TryAdd(name, value.Length > MaxPropertyLength ? value[..MaxPropertyLength] : value);
            }
        }
        catch (Exception)
        {
            // Parameters are extra information; the geometry export does not depend on them.
        }
        return result.Count == 0 ? null : result;
    }

    private async Task<ExportResult> ExportCoreAsync(ExchangeRef exchange, string outputDirectory, bool mesh, CancellationToken ct)
    {
        var sdk = Sdk();
        var identifier = Identifier(exchange);
        var revision = await GetLatestRevisionAsync(exchange, ct).ConfigureAwait(false);
        BeginTransfer(sdk, Autodesk.DataExchange.ProgressManager.Enums.Operation.Load);

        var warnings = new List<ManifestWarning>();
        var elements = new List<ManifestElement>();
        string? lengthUnit = null;
        var kept = lastModel;
        var model = kept is { } k && k.ExchangeId == exchange.ExchangeId && k.RevisionId == revision.RevisionId
                && DateTime.UtcNow - k.At < ModelLife
            ? k.Model
            : Value(await Run(() => sdk.GetElementDataModelAsync(identifier)).ConfigureAwait(false), "element data model");
        lastModel = null;   // used once: a large model is not kept in memory
        foreach (var element in model.Elements)
        {
            var sourceId = string.IsNullOrWhiteSpace(element.SourceId) ? element.UniqueId : element.SourceId;
            var category = element.Category;
            if (!element.HasGeometry)
                warnings.Add(new ManifestWarning("NO_GEOMETRY", "Element has no geometry.", sourceId));
            try
            {
                lengthUnit ??= element.LengthUnit?.ToString();
            }
            catch (Exception)
            {
            }
            elements.Add(new ManifestElement(
                sourceId,
                element.UniqueId,
                element.Name,
                category,
                element.HasGeometry,
                ToRowMajor(element.Transformation, sourceId, warnings),
                null,
                ReadProperties(element, category)));
        }

        // Whole-exchange file: the SDK converts and places every element. STEP is imported by Fusion as
        // solids; OBJ is the mesh fallback for geometry that does not survive as solids.
        Directory.CreateDirectory(Path.Combine(outputDirectory, "geometry"));
        var blob = mesh ? "geometry/exchange.obj" : "geometry/exchange.step";
        var target = Path.Combine(outputDirectory, blob);
        // The SDK takes a DIRECTORY, makes a uniquely named subfolder in it and returns the file path
        // (verified live 2026-11-16). Download to a scratch folder, then move the file into place.
        var scratch = Path.Combine(outputDirectory, "sdk-download");
        Directory.CreateDirectory(scratch);
        var download = await Run(() => Task.Run(() => mesh
                ? sdk.DownloadCompleteExchangeAsOBJ(identifier, scratch, ct)
                : sdk.DownloadCompleteExchangeAsSTEP(identifier, scratch, ct), ct))
            .ConfigureAwait(false);
        var label = mesh ? "OBJ" : "STEP";
        if (download.IsFailed)
            throw new HelperException(ErrorCodes.UnsupportedGeometry,
                $"{label} download failed: " + FirstError(download));
        var written = download.Value;
        if (string.IsNullOrWhiteSpace(written) || !File.Exists(written))
            throw new HelperException(ErrorCodes.UnsupportedGeometry, $"The exchange produced no {label} geometry.");
        File.Move(written, target, overwrite: true);
        try
        {
            Directory.Delete(scratch, recursive: true);
        }
        catch (IOException)
        {
            // Leftovers go with the export folder.
        }
        progress = null;

        var manifest = new Manifest
        {
            ExchangeId = exchange.ExchangeId,
            CollectionId = exchange.CollectionId,
            HubId = exchange.HubId,
            RevisionId = revision.RevisionId,
            ExchangeName = exchange.Name,
            CoordinateSystem = mesh
                ? "As written by the SDK's OBJ export; OBJ carries no units"
                : "As written by the SDK's STEP export; units carried in the STEP file",
            SourceLengthUnit = lengthUnit,
            ExchangeGeometry = new ManifestGeometry(mesh ? "mesh" : "step", blob),
            Elements = elements,
            Warnings = warnings,
            Source = "sdk",
        };
        var path = ManifestIO.Write(manifest, outputDirectory);
        return new ExportResult(path, outputDirectory, manifest);
    }

    public async Task<PublishResult> PublishAsync(PublishRequest request, CancellationToken ct)
    {
        var sdk = Sdk();
        var all = request.AllElements();
        if (all.Any(e => !File.Exists(e.StepPath)))
            throw new HelperException(ErrorCodes.InvalidRequest, "STEP file not found.");
        if ((request.Existing == null) == (request.New == null))
            throw new HelperException(ErrorCodes.InvalidRequest, "Set exactly one of existing / new.");

        ExchangeRef target;
        ElementDataModel model;
        var created = request.New != null;
        BeginTransfer(sdk, created
            ? Autodesk.DataExchange.ProgressManager.Enums.Operation.Create
            : Autodesk.DataExchange.ProgressManager.Enums.Operation.Update);

        if (request.New is { } fresh)
        {
            var project = await ProjectAsync(fresh.HubId, fresh.ProjectId).ConfigureAwait(false);
            var create = new ExchangeCreateRequestACC
            {
                Host = sdk.SDKOptions.HostingProvider,
                Contract = sdk.SDKOptions.ContractProvider,
                FileName = fresh.Name.Trim(),
                ACCFolderURN = fresh.FolderId,
                ProjectId = project.ProjectId,
                HubId = fresh.HubId,
                Region = project.HubRegion,
                ProjectType = project.ProjectType,
                Description = string.Empty,
            };
            var details = Value(await Run(() => sdk.CreateExchangeAsync(create, ct)).ConfigureAwait(false), "created exchange");
            target = ToRef(details, fresh.HubId, project.ProjectId, project.HubRegion);
            // A new exchange has no snapshot until its first sync, so it starts from an empty model.
            model = ElementDataModel.Create(sdk);
        }
        else
        {
            target = request.Existing!;
            var existing = target;
            model = Value(await Run(() => sdk.GetElementDataModelAsync(Identifier(existing))).ConfigureAwait(false),
                    "element data model") as ElementDataModel
                ?? throw new HelperException(ErrorCodes.SdkUnavailable, "Unexpected data model type.");
            // Full replacement of this add-in's elements; other authors' elements are left alone.
            var stale = all.Select(e => e.SourceId).ToHashSet(StringComparer.Ordinal);
            if (request.ReplaceOwned)
            {
                foreach (var old in model.Elements)
                {
                    if (old.SourceId != null && old.SourceId.StartsWith(PublishRequest.OwnedPrefix, StringComparison.Ordinal))
                        stale.Add(old.SourceId);
                }
            }
            foreach (var sourceId in stale)
                model.DeleteElementsBySourceId(sourceId);
        }

        var units = new Units(UnitFactory.MilliMeter, UnitFactory.MilliMeter, UnitFactory.Radian);
        var style = new RenderStyle("Fusion", new RGBA(160, 160, 160, 255), 1);
        var warnings = new List<string>();
        var propertyCount = 0;
        foreach (var item in all)
        {
            var element = model.AddElement(item.SourceId, item.Name);
            var path = item.StepPath;
            var geometry = await Run(() => Task.Run(() => ElementDataModel.CreateFileGeometry(
                path, GeometryFormat.Step, style, units), ct)).ConfigureAwait(false);
            model.AddElementGeometry(element, [geometry]);

            // Properties become custom instance parameters (same calls as Autodesk's console sample).
            // A property the service refuses is reported, never a reason to fail the publish.
            foreach (var (name, value) in item.Properties ?? [])
            {
                if (string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(value))
                    continue;
                try
                {
                    var parameter = new Autodesk.DataExchange.DataModels.Parameter(name, value)
                    {
                        IsCustomParameter = true,
                        SampleText = string.Empty,
                        Description = string.Empty,
                        ReadOnly = false,
                        GroupID = Autodesk.Parameters.Group.General.DisplayName(),
                    };
                    await element.CreateInstanceParameterAsync(parameter).ConfigureAwait(false);
                    propertyCount++;
                }
                catch (Exception ex)
                {
                    if (warnings.Count < 10)
                        warnings.Add($"Property '{name}' on '{item.Name}' was not published: {ex.Message}");
                }
            }
        }
        SetUpAxis(model, request.UpAxis);

        var targetIdentifier = Identifier(target);
        var sync = await Run(() => sdk.SyncExchangeDataAsync(targetIdentifier, model, ct)).ConfigureAwait(false);
        if (sync.IsFailed)
            throw new HelperException(ErrorCodes.InternalError, "Publish failed: " + FirstError(sync));

        var revision = await GetLatestRevisionAsync(target, ct).ConfigureAwait(false);
        return new PublishResult(target, revision, created, all.Count, propertyCount, warnings);
    }

    /// <summary>The scope claim of the access token, for diagnostics. The token itself is never kept or logged.</summary>
    private static string? ReadScopes(string? token)
    {
        try
        {
            var parts = token?.Split('.');
            if (parts is not { Length: 3 })
                return null;
            var payload = parts[1].Replace('-', '+').Replace('_', '/');
            payload = payload.PadRight(payload.Length + (4 - payload.Length % 4) % 4, '=');
            using var doc = System.Text.Json.JsonDocument.Parse(Convert.FromBase64String(payload));
            if (!doc.RootElement.TryGetProperty("scope", out var scope))
                return null;
            return scope.ValueKind == System.Text.Json.JsonValueKind.Array
                ? string.Join(' ', scope.EnumerateArray().Select(s => s.GetString()))
                : scope.GetString();
        }
        catch (Exception)
        {
            return null;
        }
    }

    public async Task<byte[]?> GetThumbnailAsync(string hubId, string projectId, string fileUrn, CancellationToken ct)
    {
        var hosting = Hosting();
        if (thumbnails.TryGetValue(fileUrn, out var cached))
            return cached.Length == 0 ? null : cached;
        var project = await ProjectAsync(hubId, projectId).ConfigureAwait(false);
        var descriptor = await Run(() => hosting.GetExchangeMetadataDescriptorAsync(project.ProjectId, fileUrn)).ConfigureAwait(false);
        Remember(fileUrn, descriptor?.Metadata?.ThumbnailsImage);
        return thumbnails.TryGetValue(fileUrn, out var fresh) && fresh.Length > 0 ? fresh : null;
    }

    /// <summary>Runtime type of the last thumbnail object the SDK returned (diagnostics only).</summary>
    public string? LastThumbnailType { get; private set; }

    private void Remember(string fileUrn, object? image)
    {
        LastThumbnailType = image?.GetType().FullName ?? "null";
        thumbnails[fileUrn] = ThumbnailImage.ToPng(image) ?? [];
    }

    private Client Sdk() =>
        client ?? throw new HelperException(ErrorCodes.AuthRequired, "Sign in first.");

    private IHostingProvider Hosting() =>
        Sdk().SDKOptions?.HostingProvider
        ?? throw new HelperException(ErrorCodes.SdkUnavailable, "The SDK has no hosting provider.");

    private async Task<ProjectInfo> ProjectAsync(string hubId, string projectId)
    {
        if (projects.TryGetValue(projectId, out var cached))
            return cached;
        var project = await Run(() => Hosting().GetProjectInformationAsync(hubId, projectId)).ConfigureAwait(false)
            ?? throw new HelperException(ErrorCodes.NotFound, "Project not found.");
        projects[project.ProjectId] = project;
        projects[projectId] = project;
        return project;
    }

    private static DataExchangeIdentifier Identifier(ExchangeRef exchange) => new()
    {
        ExchangeId = exchange.ExchangeId,
        CollectionId = exchange.CollectionId,
        HubId = exchange.HubId,
    };

    private static ExchangeRef ToRef(ExchangeDetails details, string hubId, string projectId, string? region) => new(
        details.ExchangeID,
        details.CollectionID,
        details.HubId ?? hubId,
        projectId,
        details.FileUrn,
        details.DisplayName,
        details.ProjectName,
        details.FolderUrn,
        region);

    private static double[]? ToRowMajor(object? transformation, string sourceId, List<ManifestWarning> warnings)
    {
        // The SDK does not document the layout of Transformation.Matrix. Until the spike confirms it
        // against the fixture exchange, transforms are reported as unknown rather than guessed; the
        // STEP export already carries placement, so import does not depend on this.
        if (transformation != null)
            warnings.Add(new ManifestWarning("TRANSFORM_UNVERIFIED",
                "Element transform not exported: SDK matrix layout not yet verified.", sourceId));
        return null;
    }

    private static T Value<T>(IResponse<T> response, string what)
    {
        if (response.IsFailed)
        {
            if (DataOnlyExchange.Matches((response.Errors ?? []).Select(e => e?.Message)))
                throw new HelperException(ErrorCodes.DataOnlyExchange, DataOnlyExchange.Message);
            throw Classify(FirstError(response), $"Could not get {what}");
        }
        return response.Value ?? throw new HelperException(ErrorCodes.NotFound, $"No {what} returned.");
    }

    private static string FirstError(IResponseBase response) =>
        response.Errors?.FirstOrDefault()?.Message ?? "Unknown error";

    /// <summary>Runs an SDK call and maps its failures onto the helper's error codes.</summary>
    private static async Task<T> Run<T>(Func<Task<T>> call)
    {
        try
        {
            return await call().ConfigureAwait(false);
        }
        catch (HelperException)
        {
            throw;
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception ex)
        {
            throw Classify(ex.Message, "Autodesk Data Exchange call failed", ex);
        }
    }

    private static HelperException Classify(string detail, string prefix, Exception? inner = null)
    {
        if (DataOnlyExchange.Matches([detail, inner?.Message, inner?.InnerException?.Message]))
            return new HelperException(ErrorCodes.DataOnlyExchange, DataOnlyExchange.Message, inner);
        var code = ErrorCodes.InternalError;
        var status = StatusOf(inner) ?? StatusOf(inner?.InnerException);
        if (status == 401)
            code = ErrorCodes.AuthRequired;
        else if (status == 403)
            code = ErrorCodes.Forbidden;
        else if (status == 404)
            code = ErrorCodes.NotFound;
        else if (inner is HttpRequestException or System.Net.Sockets.SocketException or TimeoutException
            || inner?.InnerException is HttpRequestException or System.Net.Sockets.SocketException)
            code = ErrorCodes.NetworkError;
        else if (Has(detail, "401") || Has(detail, "unauthor"))
            code = ErrorCodes.AuthRequired;
        else if (Has(detail, "403") || Has(detail, "forbidden") || Has(detail, "permission"))
            code = ErrorCodes.Forbidden;
        else if (Has(detail, "404") || Has(detail, "not found"))
            code = ErrorCodes.NotFound;
        return new HelperException(code, $"{prefix}: {detail}", inner);

        static bool Has(string text, string token) => text.Contains(token, StringComparison.OrdinalIgnoreCase);

        // Exchange and hosting errors both carry the HTTP status through IStatusCodeException.
        static int? StatusOf(Exception? error) =>
            error is Autodesk.DataExchange.Exceptions.IStatusCodeException coded ? (int)coded.StatusCode : null;
    }
}
