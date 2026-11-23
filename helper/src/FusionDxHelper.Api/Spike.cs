using System.Diagnostics;
using FusionDxHelper.Core;

namespace FusionDxHelper.Api;

public sealed record SpikeOptions(string OutputRoot, string? Url, string? PublishStepPath, string? PublishName, string? PublishStepPath2, bool ThumbnailOnly = false);

/// <summary>The ids an Autodesk Forma file URL carries: project GUID, folder URN, file (lineage) URN.</summary>
public sealed record DocsUrl(string ProjectGuid, string? FolderUrn, string? FileUrn)
{
    public static DocsUrl Parse(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri))
            throw new HelperException(ErrorCodes.InvalidRequest, "Not a URL.");
        var segments = uri.AbsolutePath.Split('/', StringSplitOptions.RemoveEmptyEntries);
        var index = Array.IndexOf(segments, "projects");
        if (index < 0 || index + 1 >= segments.Length || !Guid.TryParse(segments[index + 1], out _))
            throw new HelperException(ErrorCodes.InvalidRequest, "The URL has no project id.");

        string? folder = null, file = null;
        foreach (var pair in uri.Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var parts = pair.Split('=', 2);
            if (parts.Length != 2)
                continue;
            var value = Uri.UnescapeDataString(parts[1]);
            if (parts[0] == "folderUrn") folder = value;
            if (parts[0] == "entityId") file = value;
        }
        return new DocsUrl(segments[index + 1].ToLowerInvariant(), folder, file);
    }
}

/// <summary>
/// Milestone 0 console spike: sign in, pick one exchange (by menu or by Docs URL), print what the
/// service returns and export it. With --publish it also publishes a STEP file as a new exchange in
/// the URL's folder and reads it back. Runs through IExchangeService, the same path the add-in uses.
/// </summary>
internal static class Spike
{
    public static async Task<int> RunAsync(IExchangeService service, SpikeOptions options)
    {
        var ct = CancellationToken.None;
        Console.WriteLine($"Data Exchange spike | helper {HelperVersion.Value} | mode {service.Mode} | SDK {service.SdkVersion ?? "n/a"}");
        if (service.Mode == "mock")
            Console.WriteLine("MOCK MODE: fixture data only. This does not prove cloud access.");
        if (service.CallbackUrl != null)
            Console.WriteLine($"OAuth callback (must be registered on the APS app): {service.CallbackUrl}");

        try
        {
            Console.WriteLine("Signing in (your browser will open)...");
            var account = await service.LoginAsync(ct);
            Console.WriteLine($"Signed in as {account.DisplayName} <{account.Email}>");
            Console.WriteLine($"Token scopes: {account.Scopes ?? "(not readable)"}");

            if (options.Url != null)
                return await RunFromUrlAsync(service, options, ct);

            var hub = Pick("Hub", await service.ListHubsAsync(ct), h => $"{h.Name} [{h.Region}]");
            if (hub == null) return 1;
            var project = Pick("Project", await service.ListProjectsAsync(hub.HubId, ct), p => $"{p.Name} ({p.ProjectType})");
            if (project == null) return 1;

            FolderDto? folder = null;
            IReadOnlyList<ExchangeListItem> exchanges = [];
            while (true)
            {
                var children = await service.ListFoldersAsync(hub.HubId, project.ProjectId, folder?.FolderId, ct);
                if (folder != null)
                {
                    exchanges = await service.ListExchangesAsync(hub.HubId, project.ProjectId, folder.FolderId, ct);
                    Console.WriteLine($"Folder '{folder.Name}': {exchanges.Count} exchange(s), {children.Count} subfolder(s)");
                    if (exchanges.Count > 0 && (children.Count == 0 || Ask("Use an exchange from this folder? [Y/n]") is "" or "y" or "Y"))
                        break;
                }
                var next = Pick("Folder", children, f => f.Name);
                if (next == null) return 1;
                folder = next;
            }

            var item = Pick("Exchange", exchanges, e => e.Name);
            if (item == null) return 1;

            var exchange = await service.ResolveExchangeAsync(hub.HubId, project.ProjectId, item.FileUrn, ct);
            await ReportAsync(service, exchange, options.OutputRoot, "read", ct);
            Console.WriteLine(service.Mode == "sdk"
                ? "SPIKE PASSED: a real exchange was read and exported. Open the STEP file in Fusion to check scale and placement."
                : "Mock spike finished. Live integration is still unproven.");
            return 0;
        }
        catch (HelperException ex)
        {
            Console.WriteLine($"SPIKE FAILED [{ex.Code}]: {ex.Message}");
            return 1;
        }
    }

    private static async Task<int> RunFromUrlAsync(IExchangeService service, SpikeOptions options, CancellationToken ct)
    {
        var url = DocsUrl.Parse(options.Url!);
        Console.WriteLine($"URL project {url.ProjectGuid} | folder {url.FolderUrn ?? "-"} | file {url.FileUrn ?? "-"}");

        // The URL does not name the hub, so look for the project in each hub the user can see.
        HubDto? hub = null;
        ProjectDto? project = null;
        foreach (var candidate in await service.ListHubsAsync(ct))
        {
            IReadOnlyList<ProjectDto> projects;
            try
            {
                projects = await service.ListProjectsAsync(candidate.HubId, ct);
            }
            catch (HelperException ex)
            {
                Console.WriteLine($"  hub '{candidate.Name}': skipped [{ex.Code}]");
                continue;
            }
            project = projects.FirstOrDefault(p => p.ProjectId.Contains(url.ProjectGuid, StringComparison.OrdinalIgnoreCase));
            if (project != null)
            {
                hub = candidate;
                break;
            }
        }
        if (hub == null || project == null)
        {
            Console.WriteLine("SPIKE FAILED [NOT_FOUND]: the project in the URL is not in any hub this account can see.");
            return 1;
        }
        Console.WriteLine($"Hub           : {hub.Name} [{hub.Region}] ({hub.HubId})");
        Console.WriteLine($"Project       : {project.Name} ({project.ProjectType}) ({project.ProjectId})");

        var failed = false;
        if (url.FileUrn != null)
        {
            Console.WriteLine();
            Console.WriteLine("== Read the exchange in the URL ==");
            try
            {
                var exchange = await service.ResolveExchangeAsync(hub.HubId, project.ProjectId, url.FileUrn, ct);
                var png = await service.GetThumbnailAsync(hub.HubId, project.ProjectId, url.FileUrn, ct);
                var kind = service.GetType().GetProperty("LastThumbnailType")?.GetValue(service) as string;
                Console.WriteLine($"Thumbnail     : {(png == null ? "none" : png.Length + " bytes PNG")} (SDK object type: {kind ?? "n/a"})");
                if (png != null)
                {
                    Directory.CreateDirectory(options.OutputRoot);
                    var preview = Path.Combine(options.OutputRoot, "thumbnail.png");
                    File.WriteAllBytes(preview, png);
                    Console.WriteLine($"Thumbnail file: {preview}");
                }
                if (options.ThumbnailOnly)
                    return 0;
                await ReportAsync(service, exchange, options.OutputRoot, "read", ct);
                Console.WriteLine("READ PASSED");
            }
            catch (HelperException ex)
            {
                failed = true;
                Console.WriteLine($"READ FAILED [{ex.Code}]: {ex.Message}");
            }
        }

        if (options.PublishStepPath != null)
        {
            Console.WriteLine();
            Console.WriteLine("== Publish a new exchange into the URL's folder ==");
            try
            {
                if (url.FolderUrn == null)
                    throw new HelperException(ErrorCodes.InvalidRequest, "The URL has no folderUrn to publish into.");
                var name = options.PublishName ?? $"DX add-in test {DateTime.Now:yyyyMMdd-HHmmss}";
                var watch = Stopwatch.StartNew();
                var published = await service.PublishAsync(new PublishRequest
                {
                    New = new NewExchangeTarget(hub.HubId, project.ProjectId, url.FolderUrn, name),
                    StepPath = options.PublishStepPath,
                    ElementSourceId = "dx-addin-spike",
                    ElementName = "Building Core fixture",
                    Category = "Generic Models",
                }, ct);
                Console.WriteLine($"Published '{name}' in {watch.Elapsed.TotalSeconds:F1} s, revision {published.Revision.RevisionId}");
                await ReportAsync(service, published.Exchange, options.OutputRoot, "published-rev1", ct);

                if (options.PublishStepPath2 != null)
                {
                    Console.WriteLine();
                    Console.WriteLine("== Publish a second revision and check refresh sees it ==");
                    watch.Restart();
                    var second = await service.PublishAsync(new PublishRequest
                    {
                        Existing = published.Exchange,
                        StepPath = options.PublishStepPath2,
                        ElementSourceId = "dx-addin-spike",
                        ElementName = "Building Core fixture",
                        Category = "Generic Models",
                    }, ct);
                    Console.WriteLine($"Updated in {watch.Elapsed.TotalSeconds:F1} s, revision {second.Revision.RevisionId}");
                    Console.WriteLine(second.Revision.RevisionId != published.Revision.RevisionId
                        ? "Revision id changed: a refresh would pick this up."
                        : "WARNING: revision id did NOT change after the second publish.");
                    await ReportAsync(service, published.Exchange, options.OutputRoot, "published-rev2", ct);
                }
                Console.WriteLine("PUBLISH PASSED");
            }
            catch (HelperException ex)
            {
                failed = true;
                Console.WriteLine($"PUBLISH FAILED [{ex.Code}]: {ex.Message}");
            }
        }

        return failed ? 1 : 0;
    }

    private static async Task ReportAsync(IExchangeService service, ExchangeRef exchange, string outputRoot, string label, CancellationToken ct)
    {
        Console.WriteLine($"Exchange name : {exchange.Name}");
        Console.WriteLine($"Exchange id   : {exchange.ExchangeId}");
        Console.WriteLine($"Collection id : {exchange.CollectionId}");
        Console.WriteLine($"Hub id        : {exchange.HubId}");
        Console.WriteLine($"File URN      : {exchange.FileUrn}");

        var revision = await service.GetLatestRevisionAsync(exchange, ct);
        Console.WriteLine($"Latest rev    : {revision.RevisionId} ({revision.LastModifiedUtc:u}), {revision.RevisionCount} revision(s)");

        var directory = Path.Combine(outputRoot, $"{DateTime.UtcNow:yyyyMMdd-HHmmss}-{label}");
        Directory.CreateDirectory(directory);
        var watch = Stopwatch.StartNew();
        var export = await service.ExportAsync(exchange, directory, ct);
        watch.Stop();

        var manifest = export.Manifest;
        Console.WriteLine($"Elements      : {manifest.Elements.Count} ({manifest.Elements.Count(e => e.HasGeometry)} with geometry)");
        foreach (var group in manifest.Elements.GroupBy(e => e.Category ?? "(none)").OrderByDescending(g => g.Count()).Take(15))
            Console.WriteLine($"  {group.Count(),5}  {group.Key}");
        foreach (var element in manifest.Elements.Take(5))
            Console.WriteLine($"  sample: {element.DisplayName} | sourceId {element.SourceElementId} | category {element.Category}");
        Console.WriteLine($"Warnings      : {manifest.Warnings.Count}");
        foreach (var group in manifest.Warnings.GroupBy(w => w.Code))
            Console.WriteLine($"  {group.Count(),5}  {group.Key}");
        if (manifest.ExchangeGeometry?.BlobRef is { } blob)
        {
            var file = new FileInfo(Path.Combine(directory, blob));
            Console.WriteLine($"Geometry      : {manifest.ExchangeGeometry.Kind}, {file.Length:N0} bytes -> {file.FullName}");
        }
        Console.WriteLine($"Export time   : {watch.Elapsed.TotalSeconds:F1} s");
        Console.WriteLine($"Manifest      : {export.ManifestPath}");
    }

    private static T? Pick<T>(string label, IReadOnlyList<T> items, Func<T, string> text) where T : class
    {
        if (items.Count == 0)
        {
            Console.WriteLine($"No {label.ToLowerInvariant()} available.");
            return null;
        }
        if (items.Count == 1)
        {
            Console.WriteLine($"{label}: {text(items[0])}");
            return items[0];
        }
        for (var i = 0; i < items.Count; i++)
            Console.WriteLine($"  {i + 1,3}. {text(items[i])}");
        while (true)
        {
            var answer = Ask($"{label} number (blank to cancel)");
            if (answer.Length == 0)
                return null;
            if (int.TryParse(answer, out var n) && n >= 1 && n <= items.Count)
                return items[n - 1];
        }
    }

    private static string Ask(string prompt)
    {
        Console.Write(prompt + ": ");
        return Console.ReadLine()?.Trim() ?? string.Empty;
    }
}
