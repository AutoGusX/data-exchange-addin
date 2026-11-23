namespace FusionDxHelper.Core;

/// <summary>
/// Fixture-backed stand-in for the Autodesk cloud. Every manifest it writes is marked source "mock",
/// and health reports mode "mock", so fixture data can never pass for a real exchange.
/// </summary>
public sealed class MockExchangeService : IExchangeService
{
    public const string HubId = "mock-hub";
    public const string ProjectId = "mock-project";
    public const string FolderId = "mock-folder";
    public const string FixtureExchangeId = "mock-exchange-building-core";

    private sealed class MockExchange
    {
        public required ExchangeRef Ref { get; init; }
        /// <summary>One STEP file per revision, oldest first.</summary>
        public List<string> RevisionFiles { get; } = [];
        /// <summary>How many of <see cref="RevisionFiles"/> are "published" so far.</summary>
        public int Published { get; set; } = 1;
        public List<ManifestElement> Elements { get; init; } = [];
    }

    private readonly object gate = new();
    private readonly Dictionary<string, MockExchange> exchanges = new(StringComparer.Ordinal);
    private readonly string publishDirectory;
    private AccountInfo? account;
    private readonly HashSet<string> watched = new(StringComparer.Ordinal);
    private readonly Dictionary<string, ExchangeUpdate> updates = new(StringComparer.Ordinal);

    /// <param name="fixtureDirectory">Folder holding building-core-rev1.step and building-core-rev2.step.</param>
    public MockExchangeService(string fixtureDirectory, string? publishDirectory = null)
    {
        this.publishDirectory = publishDirectory
            ?? Path.Combine(Path.GetTempPath(), "FusionDxHelper", "mock-published");

        var rev1 = Path.Combine(fixtureDirectory, "building-core-rev1.step");
        var rev2 = Path.Combine(fixtureDirectory, "building-core-rev2.step");
        if (!File.Exists(rev1) || !File.Exists(rev2))
            throw new HelperException(ErrorCodes.NotConfigured, $"Mock fixtures not found in {fixtureDirectory}.");

        var fixture = new MockExchange
        {
            Ref = new ExchangeRef(FixtureExchangeId, "mock-collection", HubId, ProjectId,
                "urn:mock:file:building-core", "Building Core (mock)", "Mock Project", FolderId),
            Elements =
            [
                Element("mock-wall-1", "Basic Wall", "Walls"),
                Element("mock-floor-1", "Floor Slab", "Floors"),
                Element("mock-column-1", "Structural Column", "Structural Columns"),
                Element("mock-column-2", "Structural Column", "Structural Columns"),
                Element("mock-beam-1", "Beam", "Structural Framing"),
                Element("mock-brace-1", "Brace", "Structural Framing"),
            ],
        };
        fixture.RevisionFiles.Add(rev1);
        fixture.RevisionFiles.Add(rev2);
        exchanges[FixtureExchangeId] = fixture;
    }

    public string Mode => "mock";
    public string? SdkVersion => null;
    public bool Configured => true;
    public string? CallbackUrl => null;
    public AccountInfo? CurrentAccount => account;

    /// <summary>When set, the next cloud call fails with this code. Lets tests drive failure states.</summary>
    public string? FailNextWith { get; set; }

    /// <summary>Mock-only: "publish" the next fixture revision. Returns false when none is left.</summary>
    public bool AdvanceRevision(string exchangeId)
    {
        lock (gate)
        {
            if (!exchanges.TryGetValue(exchangeId, out var exchange) || exchange.Published >= exchange.RevisionFiles.Count)
                return false;
            exchange.Published++;
            if (watched.Contains(exchangeId))
                updates[exchangeId] = new ExchangeUpdate(exchangeId, Revision(exchange).RevisionId, DateTime.UtcNow);
            return true;
        }
    }

    public Task<AccountInfo> LoginAsync(CancellationToken ct)
    {
        account = new AccountInfo("mock-user", "Mock User", "mock.user@example.com");
        return Task.FromResult(account);
    }

    public Task LogoutAsync(CancellationToken ct)
    {
        account = null;
        return Task.CompletedTask;
    }

    public Task<IReadOnlyList<HubDto>> ListHubsAsync(CancellationToken ct)
    {
        Guard();
        return Task.FromResult<IReadOnlyList<HubDto>>([new HubDto(HubId, "Mock Hub", "US")]);
    }

    public Task<IReadOnlyList<ProjectDto>> ListProjectsAsync(string hubId, CancellationToken ct)
    {
        Guard();
        if (hubId != HubId)
            throw new HelperException(ErrorCodes.NotFound, "Hub not found.");
        return Task.FromResult<IReadOnlyList<ProjectDto>>([new ProjectDto(ProjectId, "Mock Project", HubId, "ACC")]);
    }

    public Task<IReadOnlyList<FolderDto>> ListFoldersAsync(string hubId, string projectId, string? parentFolderId, CancellationToken ct)
    {
        Guard();
        IReadOnlyList<FolderDto> folders = parentFolderId == null
            ? [new FolderDto(FolderId, "Project Files", false, false)]
            : [];
        return Task.FromResult(folders);
    }

    public Task<IReadOnlyList<ExchangeListItem>> ListExchangesAsync(string hubId, string projectId, string folderId, CancellationToken ct)
    {
        Guard();
        lock (gate)
        {
            IReadOnlyList<ExchangeListItem> items = exchanges.Values
                .Where(e => e.Ref.FolderUrn == folderId)
                .Select(e => new ExchangeListItem(e.Ref.FileUrn!, e.Ref.Name!))
                .ToList();
            return Task.FromResult(items);
        }
    }

    public Task<ExchangeRef> ResolveExchangeAsync(string hubId, string projectId, string fileUrn, CancellationToken ct)
    {
        Guard();
        lock (gate)
        {
            var match = exchanges.Values.FirstOrDefault(e => e.Ref.FileUrn == fileUrn)
                ?? throw new HelperException(ErrorCodes.NotFound, "Exchange not found.");
            return Task.FromResult(match.Ref);
        }
    }

    public Task<RevisionInfo> GetLatestRevisionAsync(ExchangeRef exchange, CancellationToken ct)
    {
        Guard();
        lock (gate)
        {
            var found = Find(exchange.ExchangeId);
            return Task.FromResult(Revision(found));
        }
    }

    public Task<ExportResult> ExportMeshAsync(ExchangeRef exchange, string outputDirectory, CancellationToken ct)
    {
        Guard();
        throw new HelperException(ErrorCodes.UnsupportedGeometry, "The mock has no mesh geometry.");
    }

    public Task<RevisionChanges> GetChangesAsync(ExchangeRef exchange, string fromRevisionId, CancellationToken ct)
    {
        Guard();
        lock (gate)
        {
            var found = Find(exchange.ExchangeId);
            var latest = Revision(found);
            const string prefix = "mock-rev-";
            var baseFound = fromRevisionId.StartsWith(prefix, StringComparison.Ordinal)
                && int.TryParse(fromRevisionId[prefix.Length..], out _);
            var from = baseFound ? int.Parse(fromRevisionId[prefix.Length..]) : 0;
            var behind = Math.Max(0, found.Published - from);
            // The fixture's second revision adds one column; nothing else in the mock changes elements.
            var created = new List<ElementChange>();
            if (found.Ref.ExchangeId == FixtureExchangeId && from < 2 && found.Published >= 2)
                created.Add(new ElementChange("mock-column-3", "Structural Column", "Structural Columns"));
            return Task.FromResult(new RevisionChanges(fromRevisionId, latest.RevisionId, behind, baseFound,
                created.Count, 0, 0, created, [], []));
        }
    }

    public Task<ExportResult> ExportAsync(ExchangeRef exchange, string outputDirectory, CancellationToken ct)
    {
        Guard();
        MockExchange found;
        RevisionInfo revision;
        string source;
        lock (gate)
        {
            found = Find(exchange.ExchangeId);
            revision = Revision(found);
            source = found.RevisionFiles[found.Published - 1];
        }

        Directory.CreateDirectory(Path.Combine(outputDirectory, "geometry"));
        const string blob = "geometry/exchange.step";
        File.Copy(source, Path.Combine(outputDirectory, blob), overwrite: true);

        var manifest = new Manifest
        {
            ExchangeId = found.Ref.ExchangeId,
            CollectionId = found.Ref.CollectionId,
            HubId = found.Ref.HubId,
            RevisionId = revision.RevisionId,
            ExchangeName = found.Ref.Name,
            SourceApplication = "Mock",
            CoordinateSystem = "STEP file units and origin; Z up",
            ExchangeGeometry = new ManifestGeometry("step", blob),
            Elements = found.Elements,
            Source = "mock",
        };
        var path = ManifestIO.Write(manifest, outputDirectory);
        return Task.FromResult(new ExportResult(path, outputDirectory, manifest));
    }

    public Task<PublishResult> PublishAsync(PublishRequest request, CancellationToken ct)
    {
        Guard();
        var all = request.AllElements();
        if (all.Any(e => !File.Exists(e.StepPath)))
            throw new HelperException(ErrorCodes.InvalidRequest, "STEP file not found.");
        if ((request.Existing == null) == (request.New == null))
            throw new HelperException(ErrorCodes.InvalidRequest, "Set exactly one of existing / new.");

        lock (gate)
        {
            MockExchange target;
            var created = request.New != null;
            if (request.New is { } fresh)
            {
                var id = "mock-exchange-" + Guid.NewGuid().ToString("N");
                target = new MockExchange
                {
                    Ref = new ExchangeRef(id, "mock-collection", fresh.HubId, fresh.ProjectId,
                        "urn:mock:file:" + id, fresh.Name, "Mock Project", fresh.FolderId),
                    Published = 0,
                };
                exchanges[id] = target;
            }
            else
            {
                target = Find(request.Existing!.ExchangeId);
                // Publishing over the fixture drops its unpublished future revisions.
                target.RevisionFiles.RemoveRange(target.Published, target.RevisionFiles.Count - target.Published);
            }

            Directory.CreateDirectory(publishDirectory);
            var stored = Path.Combine(publishDirectory, $"{target.Ref.ExchangeId}-rev{target.RevisionFiles.Count + 1}.step");
            File.Copy(request.StepPath, stored, overwrite: true);
            target.RevisionFiles.Add(stored);
            target.Published = target.RevisionFiles.Count;
            var ids = all.Select(e => e.SourceId).ToHashSet(StringComparer.Ordinal);
            target.Elements.RemoveAll(e => ids.Contains(e.SourceElementId)
                || (request.ReplaceOwned && e.SourceElementId.StartsWith(PublishRequest.OwnedPrefix, StringComparison.Ordinal)));
            foreach (var item in all)
                target.Elements.Add(Element(item.SourceId, item.Name, request.Category ?? "Generic Models"));

            return Task.FromResult(new PublishResult(target.Ref, Revision(target), created, all.Count,
                all.Sum(e => e.Properties?.Count ?? 0)));
        }
    }

    /// <summary>The mock has no previews; the add-in shows its "no preview" state.</summary>
    public Task DeleteExchangeAsync(string hubId, string projectId, string fileUrn, CancellationToken ct)
    {
        Guard();
        lock (gate)
        {
            var found = exchanges.Values.FirstOrDefault(e => e.Ref.FileUrn == fileUrn)
                ?? throw new HelperException(ErrorCodes.NotFound, "Exchange not found.");
            exchanges.Remove(found.Ref.ExchangeId);
            return Task.CompletedTask;
        }
    }

    public StatusInfo GetStatus()
    {
        lock (gate)
            return new StatusInfo(null, updates.Values.ToList(), watched.Count);
    }

    public Task<int> WatchAsync(IReadOnlyList<string> exchangeIds, CancellationToken ct)
    {
        Guard();
        lock (gate)
        {
            foreach (var id in exchangeIds)
                watched.Add(id);
            return Task.FromResult(watched.Count);
        }
    }

    public Task<byte[]?> GetThumbnailAsync(string hubId, string projectId, string fileUrn, CancellationToken ct)
    {
        Guard();
        return Task.FromResult<byte[]?>(null);
    }

    private void Guard()
    {
        if (FailNextWith is { } code)
        {
            FailNextWith = null;
            throw new HelperException(code, $"Mock failure: {code}.");
        }
        if (account == null)
            throw new HelperException(ErrorCodes.AuthRequired, "Sign in first.");
    }

    private MockExchange Find(string exchangeId) =>
        exchanges.TryGetValue(exchangeId, out var found)
            ? found
            : throw new HelperException(ErrorCodes.NotFound, "Exchange not found.");

    private static RevisionInfo Revision(MockExchange exchange) =>
        new($"mock-rev-{exchange.Published}", null, exchange.Published);

    private static ManifestElement Element(string id, string name, string category) =>
        new(id, id, name, category, true, Transforms.Identity, null,
            new Dictionary<string, string> { ["sourceCategory"] = category });
}
