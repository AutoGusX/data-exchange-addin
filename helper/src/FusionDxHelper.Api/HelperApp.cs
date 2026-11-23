using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using FusionDxHelper.Core;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;

namespace FusionDxHelper.Api;

public sealed record ResolveBody(string HubId, string ProjectId, string FileUrn);
public sealed record ExchangeBody(ExchangeRef Exchange);
public sealed record ChangesBody(ExchangeRef Exchange, string FromRevisionId);
public sealed record WatchBody(IReadOnlyList<string> ExchangeIds);
public sealed record CleanupBody(string OutputDirectory);
public sealed record AdvanceBody(string ExchangeId);
public sealed record ThumbnailInfo(bool Available, string? Path);

/// <summary>
/// The loopback HTTP surface the Fusion add-in talks to. Bound to 127.0.0.1 only; every route except
/// /v1/health needs the per-launch bearer secret. Routes carry ids in the body, not the path, because
/// one exchange is addressed by three opaque ids (exchange, collection, hub).
/// </summary>
public static class HelperApp
{
    public const string SecretEnvVar = "DXHELPER_SECRET";

    public static WebApplication Build(IExchangeService service, string secret, string exportRoot, HelperLog log, int port = 0)
    {
        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.UseUrls($"http://127.0.0.1:{port}");
        builder.Services.ConfigureHttpJsonOptions(o =>
        {
            o.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.CamelCase;
            o.SerializerOptions.PropertyNameCaseInsensitive = true;
        });
        var app = builder.Build();
        var secretBytes = Encoding.UTF8.GetBytes(secret);

        app.Use(async (context, next) =>
        {
            var correlationId = Guid.NewGuid().ToString("N")[..12];
            context.Items["cid"] = correlationId;
            context.Response.Headers["X-Correlation-Id"] = correlationId;
            var started = DateTime.UtcNow;
            var errorCode = "-";

            try
            {
                if (context.Request.Path != "/v1/health" && !Authorized(context, secretBytes))
                {
                    errorCode = "UNAUTHORIZED";
                    await WriteError(context, 401, "UNAUTHORIZED", "Missing or wrong helper secret.", correlationId);
                    return;
                }
                await next(context);
            }
            catch (HelperException ex)
            {
                errorCode = ex.Code;
                await WriteError(context, StatusFor(ex.Code), ex.Code, ex.Message, correlationId);
            }
            catch (BadHttpRequestException ex)
            {
                errorCode = ErrorCodes.InvalidRequest;
                await WriteError(context, 400, ErrorCodes.InvalidRequest, ex.Message, correlationId);
            }
            catch (OperationCanceledException)
            {
                errorCode = "CANCELLED";
            }
            catch (Exception ex)
            {
                errorCode = ErrorCodes.InternalError;
                log.Write($"{correlationId} unhandled {ex.GetType().Name}: {ex.Message}");
                await WriteError(context, 500, ErrorCodes.InternalError, "Unexpected helper error; see the helper log.", correlationId);
            }
            finally
            {
                // Path only: query strings and bodies can hold ids, and tokens never reach this layer.
                var ms = (int)(DateTime.UtcNow - started).TotalMilliseconds;
                log.Write($"{correlationId} {context.Request.Method} {context.Request.Path} " +
                          $"status={context.Response.StatusCode} code={errorCode} ms={ms} mode={service.Mode}");
            }
        });

        app.MapGet("/v1/health", () => new HealthInfo(
            HelperVersion.Value, service.Mode, service.SdkVersion, true, service.Configured,
            service.CurrentAccount != null, service.CallbackUrl));

        app.MapPost("/v1/auth/login", async (CancellationToken ct) => await service.LoginAsync(ct));
        app.MapPost("/v1/auth/logout", async (CancellationToken ct) =>
        {
            await service.LogoutAsync(ct);
            return Results.Ok(new { signedIn = false });
        });
        app.MapGet("/v1/auth/account", () =>
            service.CurrentAccount ?? throw new HelperException(ErrorCodes.AuthRequired, "Not signed in."));

        app.MapGet("/v1/hubs", async (CancellationToken ct) => await service.ListHubsAsync(ct));
        app.MapGet("/v1/projects", async (string hubId, CancellationToken ct) =>
            await service.ListProjectsAsync(Need(hubId, "hubId"), ct));
        app.MapGet("/v1/folders", async (string hubId, string projectId, string? parentFolderId, CancellationToken ct) =>
            await service.ListFoldersAsync(Need(hubId, "hubId"), Need(projectId, "projectId"),
                string.IsNullOrWhiteSpace(parentFolderId) ? null : parentFolderId, ct));
        app.MapGet("/v1/exchanges", async (string hubId, string projectId, string folderId, CancellationToken ct) =>
            await service.ListExchangesAsync(Need(hubId, "hubId"), Need(projectId, "projectId"), Need(folderId, "folderId"), ct));

        // Preview image for the Load window. Written as a PNG file because Fusion image inputs take a path.
        app.MapGet("/v1/exchanges/thumbnail", async (string hubId, string projectId, string fileUrn, CancellationToken ct) =>
        {
            var png = await service.GetThumbnailAsync(Need(hubId, "hubId"), Need(projectId, "projectId"), Need(fileUrn, "fileUrn"), ct);
            if (png == null)
                return new ThumbnailInfo(false, null);
            var directory = Path.Combine(exportRoot, "thumbnails");
            Directory.CreateDirectory(directory);
            var path = Path.Combine(directory, Guid.NewGuid().ToString("N") + ".png");
            await File.WriteAllBytesAsync(path, png, ct);
            return new ThumbnailInfo(true, path);
        });

        app.MapPost("/v1/exchanges/resolve", async (ResolveBody body, CancellationToken ct) =>
            await service.ResolveExchangeAsync(Need(body.HubId, "hubId"), Need(body.ProjectId, "projectId"),
                Need(body.FileUrn, "fileUrn"), ct));

        app.MapPost("/v1/exchanges/latest-revision", async (ExchangeBody body, CancellationToken ct) =>
            await service.GetLatestRevisionAsync(Checked(body.Exchange), ct));

        // Polled by the add-in while jobs run: answers from memory, never from the cloud.
        app.MapGet("/v1/status", () => service.GetStatus());

        app.MapPost("/v1/events/watch", async (WatchBody body, CancellationToken ct) =>
            Results.Ok(new { watching = await service.WatchAsync(body.ExchangeIds ?? [], ct) }));

        // Destructive: the add-in asks the user to confirm before calling this.
        app.MapPost("/v1/exchanges/delete", async (ResolveBody body, CancellationToken ct) =>
        {
            await service.DeleteExchangeAsync(Need(body.HubId, "hubId"), Need(body.ProjectId, "projectId"),
                Need(body.FileUrn, "fileUrn"), ct);
            return Results.Ok(new { deleted = true });
        });

        app.MapPost("/v1/exchanges/changes", async (ChangesBody body, CancellationToken ct) =>
            await service.GetChangesAsync(Checked(body.Exchange), Need(body.FromRevisionId, "fromRevisionId"), ct));

        app.MapPost("/v1/exchanges/export", (ExchangeBody body, CancellationToken ct) => Export(body, false, ct));
        app.MapPost("/v1/exchanges/export-mesh", (ExchangeBody body, CancellationToken ct) => Export(body, true, ct));

        async Task<ExportResult> Export(ExchangeBody body, bool mesh, CancellationToken ct)
        {
            var exchange = Checked(body.Exchange);
            var directory = Path.Combine(exportRoot, Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                var result = mesh
                    ? await service.ExportMeshAsync(exchange, directory, ct)
                    : await service.ExportAsync(exchange, directory, ct);
                // Re-validate from disk: this is the last check before the add-in mutates Fusion.
                ManifestIO.Validate(ManifestIO.Read(result.ManifestPath), directory);
                return result;
            }
            catch
            {
                TryDelete(directory);
                throw;
            }
        }

        app.MapPost("/v1/exchanges/publish", async (PublishRequest body, CancellationToken ct) =>
        {
            Need(body.StepPath, "stepPath");
            Need(body.ElementSourceId, "elementSourceId");
            Need(body.ElementName, "elementName");
            foreach (var extra in body.MoreElements ?? [])
            {
                Need(extra.SourceId, "moreElements.sourceId");
                Need(extra.Name, "moreElements.name");
                Need(extra.StepPath, "moreElements.stepPath");
            }
            if (body.Existing != null)
                Checked(body.Existing);
            if (body.New is { } fresh)
            {
                Need(fresh.HubId, "new.hubId");
                Need(fresh.ProjectId, "new.projectId");
                Need(fresh.FolderId, "new.folderId");
                Need(fresh.Name, "new.name");
            }
            return await service.PublishAsync(body, ct);
        });

        app.MapPost("/v1/exports/cleanup", (CleanupBody body) =>
        {
            var root = Path.GetFullPath(exportRoot) + Path.DirectorySeparatorChar;
            var target = Path.GetFullPath(Need(body.OutputDirectory, "outputDirectory"));
            if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                throw new HelperException(ErrorCodes.InvalidRequest, "Not an export folder.");
            TryDelete(target);
            return Results.Ok(new { deleted = !Directory.Exists(target) });
        });

        if (service is MockExchangeService mock)
        {
            app.MapPost("/v1/mock/advance", (AdvanceBody body) =>
                Results.Ok(new { advanced = mock.AdvanceRevision(Need(body.ExchangeId, "exchangeId")) }));
        }

        return app;
    }

    public static int BoundPort(WebApplication app) =>
        new Uri(app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.First()).Port;

    public static string NewSecret() => Convert.ToHexString(RandomNumberGenerator.GetBytes(32));

    public static void TryDelete(string directory)
    {
        try
        {
            if (Directory.Exists(directory))
                Directory.Delete(directory, recursive: true);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private static bool Authorized(HttpContext context, byte[] secret)
    {
        var header = context.Request.Headers.Authorization.ToString();
        const string prefix = "Bearer ";
        if (!header.StartsWith(prefix, StringComparison.Ordinal))
            return false;
        var given = Encoding.UTF8.GetBytes(header[prefix.Length..]);
        return CryptographicOperations.FixedTimeEquals(given, secret);
    }

    private static string Need(string? value, string name) =>
        string.IsNullOrWhiteSpace(value)
            ? throw new HelperException(ErrorCodes.InvalidRequest, $"'{name}' is required.")
            : value;

    private static ExchangeRef Checked(ExchangeRef? exchange)
    {
        if (exchange == null)
            throw new HelperException(ErrorCodes.InvalidRequest, "'exchange' is required.");
        Need(exchange.ExchangeId, "exchange.exchangeId");
        Need(exchange.CollectionId, "exchange.collectionId");
        Need(exchange.HubId, "exchange.hubId");
        return exchange;
    }

    private static int StatusFor(string code) => code switch
    {
        ErrorCodes.AuthRequired => 401,
        ErrorCodes.Forbidden => 403,
        ErrorCodes.NotFound => 404,
        ErrorCodes.InvalidRequest => 400,
        ErrorCodes.NotConfigured => 409,
        ErrorCodes.UnsupportedGeometry => 422,
        ErrorCodes.DataOnlyExchange => 422,
        ErrorCodes.NetworkError => 503,
        ErrorCodes.SdkUnavailable => 503,
        _ => 500,
    };

    private static Task WriteError(HttpContext context, int status, string code, string message, string correlationId)
    {
        if (context.Response.HasStarted)
            return Task.CompletedTask;
        context.Response.StatusCode = status;
        return context.Response.WriteAsJsonAsync(new ErrorBody(code, message, correlationId));
    }
}

/// <summary>Append-only local log. Callers pass ids, counts, durations and error codes only.</summary>
public sealed class HelperLog(string? directory)
{
    private readonly object gate = new();

    public static HelperLog None { get; } = new(null);

    public void Write(string line)
    {
        if (directory == null)
            return;
        try
        {
            lock (gate)
            {
                Directory.CreateDirectory(directory);
                File.AppendAllText(
                    Path.Combine(directory, $"helper-{DateTime.UtcNow:yyyyMMdd}.log"),
                    $"{DateTime.UtcNow:O} v{HelperVersion.Value} {line}{Environment.NewLine}");
            }
        }
        catch (IOException) { }
    }
}
