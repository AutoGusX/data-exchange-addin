using System.Diagnostics;
using System.Text.Json;
using FusionDxHelper.Api;
using FusionDxHelper.AutodeskSdk;
using FusionDxHelper.Core;

// FusionDxHelper.exe [serve] [--mock] [--fixtures <dir>] [--parent-pid <pid>]
// FusionDxHelper.exe spike [--mock] [--fixtures <dir>] [--out <dir>]
//                          [--url <Autodesk Forma file URL>] [--publish <step> [--publish2 <step>] [--name <exchange name>]]

var mock = args.Contains("--mock");
var service = CreateService(mock, Option("--fixtures"));

if (args.Length > 0 && args[0] == "spike")
    return await Spike.RunAsync(service, new SpikeOptions(
        Option("--out") ?? Path.Combine(HelperPaths.Data, "spike"),
        Option("--url"), Option("--publish"), Option("--name"), Option("--publish2"), args.Contains("--thumbnail-only")));

var secret = Environment.GetEnvironmentVariable(HelperApp.SecretEnvVar);
if (string.IsNullOrWhiteSpace(secret))
{
    Console.Error.WriteLine($"{HelperApp.SecretEnvVar} is not set. The add-in starts the helper and supplies it.");
    return 2;
}

var exportRoot = Path.Combine(HelperPaths.Data, "exports");
HelperApp.TryDelete(exportRoot); // geometry left behind by an earlier run
Directory.CreateDirectory(exportRoot);

// Fixture data is never cached: mock revisions are advanced by hand during tests.
var cache = service.Mode == "mock" ? null : new ExportCache(Path.Combine(exportRoot, "cache"));
var app = HelperApp.Build(service, secret, exportRoot, new HelperLog(Path.Combine(HelperPaths.Data, "logs")), cache: cache);
await app.StartAsync();

// The add-in reads this one line to learn the port. The secret is never printed.
Console.WriteLine("DXHELPER_READY " + JsonSerializer.Serialize(new { port = HelperApp.BoundPort(app), mode = service.Mode }));
Console.Out.Flush();

if (int.TryParse(Option("--parent-pid"), out var parentPid))
    _ = Task.Run(async () =>
    {
        // Exit with Fusion, so no orphan helper keeps a port and a signed-in session.
        try
        {
            using var parent = Process.GetProcessById(parentPid);
            await parent.WaitForExitAsync();
        }
        catch (ArgumentException) { }
        await app.StopAsync();
    });

await app.WaitForShutdownAsync();
HelperApp.TryDelete(exportRoot);
return 0;

string? Option(string name)
{
    var index = Array.IndexOf(args, name);
    return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
}

static IExchangeService CreateService(bool mock, string? fixtures)
{
    if (mock)
        return new MockExchangeService(fixtures ?? Path.Combine(AppContext.BaseDirectory, "fixtures"));

    var config = HelperConfig.Load();
    return new SdkExchangeService(new SdkSettings(
        config.ClientId,
        config.CallbackUrl ?? SdkSettings.DefaultCallbackUrl,
        Path.Combine(HelperPaths.Data, "logs", "sdk")));
}

internal static class HelperPaths
{
    /// <summary>Per-user, not roaming: %LOCALAPPDATA%\FusionDataExchange.</summary>
    public static string Data { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FusionDataExchange");

    public static string ConfigFile { get; } = Path.Combine(Data, "config.json");
}

/// <summary>
/// Client ID and callback URL: environment (DX_CLIENT_ID, DX_CALLBACK_URL) first, then
/// %LOCALAPPDATA%\FusionDataExchange\config.json, then the config.json shipped in the add-in folder. A client ID is public (PKCE); no secret is read anywhere.
/// </summary>
internal sealed record HelperConfig(string? ClientId, string? CallbackUrl)
{
    /// <summary>
    /// The config.json shipped inside the add-in folder (the helper sits in its "helper" subfolder).
    /// It lets a packaged add-in work with no per-user setup; the per-user file still overrides it.
    /// </summary>
    public static string BundledConfigFile { get; } =
        Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "config.json"));

    public static HelperConfig Load()
    {
        string? clientId = null, callback = null;
        // Later files win: shipped defaults first, then the user's own file.
        foreach (var file in new[] { BundledConfigFile, HelperPaths.ConfigFile })
        {
            if (!File.Exists(file))
                continue;
            try
            {
                using var doc = JsonDocument.Parse(File.ReadAllText(file));
                if (doc.RootElement.TryGetProperty("clientId", out var id) && id.GetString() is { Length: > 0 } idText)
                    clientId = idText;
                if (doc.RootElement.TryGetProperty("callbackUrl", out var cb) && cb.GetString() is { Length: > 0 } cbText)
                    callback = cbText;
            }
            catch (JsonException)
            {
                Console.Error.WriteLine($"Ignoring unreadable config file {file}.");
            }
        }

        clientId = Environment.GetEnvironmentVariable("DX_CLIENT_ID") is { Length: > 0 } envId ? envId : clientId;
        callback = Environment.GetEnvironmentVariable("DX_CALLBACK_URL") is { Length: > 0 } envCb ? envCb : callback;
        if (callback != null && !callback.EndsWith('/'))
            callback += "/";
        return new HelperConfig(clientId, callback);
    }
}
