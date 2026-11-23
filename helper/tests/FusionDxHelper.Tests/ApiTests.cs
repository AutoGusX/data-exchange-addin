using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using FusionDxHelper.Api;
using FusionDxHelper.Core;
using Microsoft.AspNetCore.Builder;

namespace FusionDxHelper.Tests;

/// <summary>Drives the real HTTP surface on a real loopback port, backed by the mock service.</summary>
public sealed class ApiTests : IAsyncLifetime
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private static readonly ExchangeRef Fixture =
        new(MockExchangeService.FixtureExchangeId, "mock-collection", MockExchangeService.HubId);

    private readonly string root = Directory.CreateTempSubdirectory("dxapi").FullName;
    private readonly string secret = HelperApp.NewSecret();
    private MockExchangeService mock = null!;
    private WebApplication app = null!;
    private HttpClient http = null!;

    private static string Fixtures => Path.Combine(AppContext.BaseDirectory, "fixtures");

    public async Task InitializeAsync()
    {
        mock = new MockExchangeService(Fixtures, Path.Combine(root, "published"));
        app = HelperApp.Build(mock, secret, Path.Combine(root, "exports"), HelperLog.None);
        await app.StartAsync();
        http = new HttpClient { BaseAddress = new Uri($"http://127.0.0.1:{HelperApp.BoundPort(app)}") };
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", secret);
    }

    public async Task DisposeAsync()
    {
        http.Dispose();
        await app.StopAsync();
        await app.DisposeAsync();
        Directory.Delete(root, true);
    }

    private async Task<T> Post<T>(string path, object body)
    {
        var response = await http.PostAsJsonAsync(path, body, Json);
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());
        return (await response.Content.ReadFromJsonAsync<T>(Json))!;
    }

    private static async Task<ErrorBody> Error(HttpResponseMessage response, HttpStatusCode expected)
    {
        Assert.Equal(expected, response.StatusCode);
        return (await response.Content.ReadFromJsonAsync<ErrorBody>(Json))!;
    }

    private Task Login() => http.PostAsync("/v1/auth/login", null);

    [Fact]
    public async Task HealthIsOpenAndSaysMock()
    {
        using var anonymous = new HttpClient { BaseAddress = http.BaseAddress };
        var health = await anonymous.GetFromJsonAsync<HealthInfo>("/v1/health", Json);
        Assert.Equal("mock", health!.Mode);
        Assert.False(health.SignedIn);
    }

    [Fact]
    public async Task EverythingElseNeedsTheSecret()
    {
        using var anonymous = new HttpClient { BaseAddress = http.BaseAddress };
        Assert.Equal(HttpStatusCode.Unauthorized, (await anonymous.GetAsync("/v1/hubs")).StatusCode);
        anonymous.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", "wrong");
        Assert.Equal(HttpStatusCode.Unauthorized, (await anonymous.PostAsync("/v1/auth/login", null)).StatusCode);
    }

    [Fact]
    public async Task CloudCallsBeforeSignInReturnAuthRequired()
    {
        var error = await Error(await http.GetAsync("/v1/hubs"), HttpStatusCode.Unauthorized);
        Assert.Equal(ErrorCodes.AuthRequired, error.Code);
        Assert.False(string.IsNullOrEmpty(error.CorrelationId));
    }

    [Fact]
    public async Task BrowseResolveExportProducesAValidManifestAndStep()
    {
        await Login();
        var hubs = await http.GetFromJsonAsync<List<HubDto>>("/v1/hubs", Json);
        var projects = await http.GetFromJsonAsync<List<ProjectDto>>($"/v1/projects?hubId={hubs![0].HubId}", Json);
        var folders = await http.GetFromJsonAsync<List<FolderDto>>(
            $"/v1/folders?hubId={hubs[0].HubId}&projectId={projects![0].ProjectId}", Json);
        var exchanges = await http.GetFromJsonAsync<List<ExchangeListItem>>(
            $"/v1/exchanges?hubId={hubs[0].HubId}&projectId={projects[0].ProjectId}&folderId={folders![0].FolderId}", Json);
        var exchange = await Post<ExchangeRef>("/v1/exchanges/resolve",
            new ResolveBody(hubs[0].HubId, projects[0].ProjectId, exchanges![0].FileUrn));

        var export = await Post<ExportResult>("/v1/exchanges/export", new ExchangeBody(exchange));
        Assert.Equal("mock", export.Manifest.Source);
        Assert.Equal("mock-rev-1", export.Manifest.RevisionId);
        Assert.Equal(6, export.Manifest.Elements.Count);
        var step = Path.Combine(export.OutputDirectory, export.Manifest.ExchangeGeometry!.BlobRef!);
        Assert.StartsWith("ISO-10303-21;", File.ReadAllText(step));
        ManifestIO.Validate(ManifestIO.Read(export.ManifestPath), export.OutputDirectory);

        await Post<JsonElement>("/v1/exports/cleanup", new CleanupBody(export.OutputDirectory));
        Assert.False(Directory.Exists(export.OutputDirectory));
    }

    [Fact]
    public async Task WatchedExchangeReportsItsNewRevision()
    {
        await Login();
        await Post<JsonElement>("/v1/events/watch", new WatchBody([Fixture.ExchangeId]));
        var quiet = await http.GetFromJsonAsync<StatusInfo>("/v1/status", Json);
        Assert.Equal(1, quiet!.Watching);
        Assert.Empty(quiet.Updates);

        await Post<JsonElement>("/v1/mock/advance", new AdvanceBody(Fixture.ExchangeId));
        var status = await http.GetFromJsonAsync<StatusInfo>("/v1/status", Json);
        var update = Assert.Single(status!.Updates);
        Assert.Equal(Fixture.ExchangeId, update.ExchangeId);
        Assert.Equal("mock-rev-2", update.RevisionId);
    }

    [Fact]
    public async Task ChangesListWhatTheNewRevisionAdded()
    {
        await Login();
        var loaded = await Post<RevisionInfo>("/v1/exchanges/latest-revision", new ExchangeBody(Fixture));
        var none = await Post<RevisionChanges>("/v1/exchanges/changes", new ChangesBody(Fixture, loaded.RevisionId));
        Assert.Equal(0, none.RevisionsBehind);
        Assert.Equal(0, none.CreatedCount);

        await Post<JsonElement>("/v1/mock/advance", new AdvanceBody(Fixture.ExchangeId));
        var changes = await Post<RevisionChanges>("/v1/exchanges/changes", new ChangesBody(Fixture, loaded.RevisionId));
        Assert.Equal(1, changes.RevisionsBehind);
        Assert.True(changes.BaseFound);
        Assert.Equal("mock-column-3", Assert.Single(changes.Created).SourceElementId);
        Assert.Equal(0, changes.ModifiedCount + changes.DeletedCount);
    }

    [Fact]
    public async Task RefreshSeesNewRevisionOnlyAfterItIsPublished()
    {
        await Login();
        var before = await Post<RevisionInfo>("/v1/exchanges/latest-revision", new ExchangeBody(Fixture));
        var again = await Post<RevisionInfo>("/v1/exchanges/latest-revision", new ExchangeBody(Fixture));
        Assert.Equal(before.RevisionId, again.RevisionId);

        await Post<JsonElement>("/v1/mock/advance", new AdvanceBody(Fixture.ExchangeId));
        var after = await Post<RevisionInfo>("/v1/exchanges/latest-revision", new ExchangeBody(Fixture));
        Assert.NotEqual(before.RevisionId, after.RevisionId);

        var export = await Post<ExportResult>("/v1/exchanges/export", new ExchangeBody(Fixture));
        Assert.Contains("mock-column-3", File.ReadAllText(Path.Combine(export.OutputDirectory, "geometry", "exchange.step")));
    }

    [Fact]
    public async Task PublishCreatesAnExchangeThatCanBeLoadedBack()
    {
        await Login();
        var step = Path.Combine(root, "design.step");
        File.Copy(Path.Combine(Fixtures, "building-core-rev1.step"), step);

        var created = await Post<PublishResult>("/v1/exchanges/publish", new PublishRequest
        {
            New = new NewExchangeTarget(MockExchangeService.HubId, MockExchangeService.ProjectId, MockExchangeService.FolderId, "From Fusion"),
            StepPath = step, ElementSourceId = "fusion-root", ElementName = "Bracket",
        });
        Assert.True(created.Created);

        var updated = await Post<PublishResult>("/v1/exchanges/publish", new PublishRequest
        {
            Existing = created.Exchange, StepPath = step, ElementSourceId = "fusion-root", ElementName = "Bracket",
        });
        Assert.False(updated.Created);
        Assert.NotEqual(created.Revision.RevisionId, updated.Revision.RevisionId);

        var export = await Post<ExportResult>("/v1/exchanges/export", new ExchangeBody(created.Exchange));
        Assert.Single(export.Manifest.Elements);
        Assert.Equal("fusion-root", export.Manifest.Elements[0].SourceElementId);
    }

    [Fact]
    public async Task PublishSeveralElementsThenReplaceThemThenDelete()
    {
        await Login();
        var step = Path.Combine(root, "multi.step");
        File.Copy(Path.Combine(Fixtures, "building-core-rev1.step"), step);

        var created = await Post<PublishResult>("/v1/exchanges/publish", new PublishRequest
        {
            New = new NewExchangeTarget(MockExchangeService.HubId, MockExchangeService.ProjectId, MockExchangeService.FolderId, "Units"),
            StepPath = step, ElementSourceId = "fusion:root", ElementName = "Root",
            Properties = new() { ["Part number"] = "P-1" },
            MoreElements =
            [
                new PublishElement("fusion:c1:1", "Bracket:1", step, new() { ["Material"] = "Steel" }),
                new PublishElement("fusion:c2:1", "Motor:1", step),
            ],
        });
        Assert.Equal(3, created.ElementCount);
        Assert.Equal(2, created.PropertyCount);

        // Publishing only one component again removes the other two elements this add-in owned.
        await Post<PublishResult>("/v1/exchanges/publish", new PublishRequest
        {
            Existing = created.Exchange, StepPath = step, ElementSourceId = "fusion:c1:1", ElementName = "Bracket:1",
            ReplaceOwned = true,
        });
        var export = await Post<ExportResult>("/v1/exchanges/export", new ExchangeBody(created.Exchange));
        Assert.Equal("fusion:c1:1", Assert.Single(export.Manifest.Elements).SourceElementId);

        await Post<JsonElement>("/v1/exchanges/delete",
            new ResolveBody(MockExchangeService.HubId, MockExchangeService.ProjectId, created.Exchange.FileUrn!));
        var gone = await http.PostAsJsonAsync("/v1/exchanges/latest-revision", new ExchangeBody(created.Exchange), Json);
        Assert.Equal(System.Net.HttpStatusCode.NotFound, gone.StatusCode);
    }

    [Fact]
    public async Task PublishRejectsAmbiguousTarget()
    {
        await Login();
        var response = await http.PostAsJsonAsync("/v1/exchanges/publish", new PublishRequest
        {
            StepPath = Path.Combine(Fixtures, "building-core-rev1.step"),
            ElementSourceId = "a", ElementName = "b",
        }, Json);
        Assert.Equal(ErrorCodes.InvalidRequest, (await Error(response, HttpStatusCode.BadRequest)).Code);
    }

    [Theory]
    [InlineData(ErrorCodes.Forbidden, HttpStatusCode.Forbidden)]
    [InlineData(ErrorCodes.NetworkError, HttpStatusCode.ServiceUnavailable)]
    [InlineData(ErrorCodes.NotFound, HttpStatusCode.NotFound)]
    public async Task FailuresSurfaceAsStructuredErrorsAndLeaveNoExportBehind(string code, HttpStatusCode status)
    {
        await Login();
        mock.FailNextWith = code;
        var response = await http.PostAsJsonAsync("/v1/exchanges/export", new ExchangeBody(Fixture), Json);
        Assert.Equal(code, (await Error(response, status)).Code);
        var exports = Path.Combine(root, "exports");
        Assert.True(!Directory.Exists(exports) || Directory.GetDirectories(exports).Length == 0);
    }

    [Fact]
    public async Task CleanupRefusesPathsOutsideTheExportRoot()
    {
        var response = await http.PostAsJsonAsync("/v1/exports/cleanup", new CleanupBody(root), Json);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.True(Directory.Exists(root));
    }

    [Fact]
    public async Task MissingIdsAreRejected()
    {
        await Login();
        var response = await http.PostAsJsonAsync("/v1/exchanges/latest-revision",
            new ExchangeBody(new ExchangeRef("", "c", "h")), Json);
        Assert.Equal(ErrorCodes.InvalidRequest, (await Error(response, HttpStatusCode.BadRequest)).Code);
    }

    [Fact]
    public async Task ThumbnailReportsUnavailableInsteadOfFailing()
    {
        await Login();
        var info = await http.GetFromJsonAsync<ThumbnailInfo>(
            $"/v1/exchanges/thumbnail?hubId={MockExchangeService.HubId}&projectId={MockExchangeService.ProjectId}&fileUrn=urn:mock:file:building-core", Json);
        Assert.False(info!.Available);
        Assert.Null(info.Path);
    }

    [Fact]
    public async Task ExportCacheReturnsTheSameRevisionWithoutAnotherDownload()
    {
        await mock.LoginAsync(CancellationToken.None);
        var exchange = (await mock.ResolveExchangeAsync(MockExchangeService.HubId, MockExchangeService.ProjectId,
            "urn:mock:file:building-core", CancellationToken.None));
        var first = Path.Combine(root, "cache-test", "first");
        Directory.CreateDirectory(first);
        var export = await mock.ExportAsync(exchange, first, CancellationToken.None);
        var cache = new ExportCache(Path.Combine(root, "cache-test", "cache"));
        var second = Path.Combine(root, "cache-test", "second");
        Assert.Null(cache.TryRestore(exchange.ExchangeId, export.Manifest.RevisionId, false, second));
        cache.Store(export, false);
        var hit = cache.TryRestore(exchange.ExchangeId, export.Manifest.RevisionId, false, second);
        Assert.NotNull(hit);
        Assert.Equal(export.Manifest.RevisionId, hit!.Manifest.RevisionId);
        Assert.True(File.Exists(Path.Combine(second, hit.Manifest.ExchangeGeometry!.BlobRef!)));
        // Another revision, or the mesh form, is a miss.
        Assert.Null(cache.TryRestore(exchange.ExchangeId, "other-revision", false, Path.Combine(root, "cache-test", "third")));
        Assert.Null(cache.TryRestore(exchange.ExchangeId, export.Manifest.RevisionId, true, Path.Combine(root, "cache-test", "fourth")));
        // Over the size limit the entry is dropped rather than kept forever.
        var tiny = new ExportCache(Path.Combine(root, "cache-test", "tiny"), maxBytes: 1);
        tiny.Store(export, false);
        tiny.Store(export with { Manifest = export.Manifest with { RevisionId = "newer" } }, false);
        Assert.Null(tiny.TryRestore(exchange.ExchangeId, export.Manifest.RevisionId, false, Path.Combine(root, "cache-test", "fifth")));
    }

    [Fact]
    public void AecDataModelRefusalIsRecognised()
    {
        // The text the service returned for a real AEC Data Model exchange (helper log, 2026-11-17).
        Assert.True(DataOnlyExchange.Matches(["The request is invalid.",
            "The request is invalid: This API does not support AECDM based exchange."]));
        Assert.False(DataOnlyExchange.Matches(["The request is invalid.", null, "Not found"]));
    }

    [Fact]
    public async Task SignOutEndsTheSession()
    {
        await Login();
        Assert.True((await http.GetAsync("/v1/hubs")).IsSuccessStatusCode);
        await http.PostAsync("/v1/auth/logout", null);
        Assert.Equal(HttpStatusCode.Unauthorized, (await http.GetAsync("/v1/hubs")).StatusCode);
    }
}
