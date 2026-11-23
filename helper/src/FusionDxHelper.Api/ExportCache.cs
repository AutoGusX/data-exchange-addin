using FusionDxHelper.Core;

namespace FusionDxHelper.Api;

/// <summary>
/// Keeps finished exports for the life of this helper process, keyed by exchange and revision, so
/// the same revision is downloaded once per Fusion session however often it is loaded.
/// It lives inside the export root, which is wiped when the helper starts and stops: no model data
/// outlives the session.
/// </summary>
public sealed class ExportCache(string root, long maxBytes = ExportCache.DefaultMaxBytes)
{
    public const long DefaultMaxBytes = 2L * 1024 * 1024 * 1024;
    private readonly object gate = new();

    private string Folder(string exchangeId, string revisionId, bool mesh) =>
        Path.Combine(root, Safe(exchangeId), Safe(revisionId) + (mesh ? "-mesh" : "-step"));

    private static string Safe(string text)
    {
        var chars = text.Select(c => char.IsLetterOrDigit(c) || c is '-' or '_' or '.' ? c : '_').ToArray();
        var safe = new string(chars).Trim('.');
        return safe.Length == 0 ? "_" : safe.Length > 120 ? safe[..120] : safe;
    }

    /// <summary>Copies a cached export into <paramref name="directory"/>, or returns null when there is none.</summary>
    public ExportResult? TryRestore(string exchangeId, string revisionId, bool mesh, string directory)
    {
        lock (gate)
        {
            var source = Folder(exchangeId, revisionId, mesh);
            if (!File.Exists(Path.Combine(source, ManifestIO.ManifestFileName)))
                return null;
            try
            {
                Copy(source, directory);
                var path = Path.Combine(directory, ManifestIO.ManifestFileName);
                var manifest = ManifestIO.Read(path);
                if (manifest.RevisionId != revisionId)
                    return null;
                ManifestIO.Validate(manifest, directory);
                Directory.SetLastWriteTimeUtc(source, DateTime.UtcNow);
                return new ExportResult(path, directory, manifest);
            }
            catch (Exception)
            {
                // A damaged entry is dropped and the caller downloads again.
                HelperApp.TryDelete(source);
                return null;
            }
        }
    }

    /// <summary>Keeps a copy of a finished export. Never fails the export it is called for.</summary>
    public void Store(ExportResult result, bool mesh)
    {
        lock (gate)
        {
            try
            {
                var target = Folder(result.Manifest.ExchangeId, result.Manifest.RevisionId, mesh);
                HelperApp.TryDelete(target);
                Copy(result.OutputDirectory, target);
                Directory.SetLastWriteTimeUtc(target, DateTime.UtcNow);
                Evict(target);
            }
            catch (Exception)
            {
            }
        }
    }

    private void Evict(string keep)
    {
        if (!Directory.Exists(root))
            return;
        var entries = Directory.GetDirectories(root).SelectMany(Directory.GetDirectories)
            .Select(path => (path, used: Size(path), at: Directory.GetLastWriteTimeUtc(path)))
            .OrderBy(entry => entry.at).ToList();
        var total = entries.Sum(entry => entry.used);
        foreach (var entry in entries)
        {
            if (total <= maxBytes)
                break;
            if (string.Equals(entry.path, keep, StringComparison.OrdinalIgnoreCase) && entries.Count > 1)
                continue;
            HelperApp.TryDelete(entry.path);
            total -= entry.used;
        }
    }

    private static long Size(string directory) =>
        Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories).Sum(file => new FileInfo(file).Length);

    private static void Copy(string source, string target)
    {
        Directory.CreateDirectory(target);
        foreach (var directory in Directory.EnumerateDirectories(source, "*", SearchOption.AllDirectories))
            Directory.CreateDirectory(Path.Combine(target, Path.GetRelativePath(source, directory)));
        foreach (var file in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
            File.Copy(file, Path.Combine(target, Path.GetRelativePath(source, file)), overwrite: true);
    }
}
