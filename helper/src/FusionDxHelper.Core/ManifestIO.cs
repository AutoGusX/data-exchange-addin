using System.Text.Json;

namespace FusionDxHelper.Core;

public static class ManifestIO
{
    public const string ManifestFileName = "manifest.json";

    public static string Write(Manifest manifest, string outputDirectory)
    {
        Validate(manifest, outputDirectory);
        var path = Path.Combine(outputDirectory, ManifestFileName);
        File.WriteAllText(path, JsonSerializer.Serialize(manifest, ManifestJsonContext.Default.Manifest));
        return path;
    }

    public static Manifest Read(string manifestPath) =>
        JsonSerializer.Deserialize(File.ReadAllText(manifestPath), ManifestJsonContext.Default.Manifest)
        ?? throw new HelperException(ErrorCodes.InternalError, "Manifest is empty.");

    /// <summary>
    /// Checks the manifest before the add-in is allowed to touch Fusion: ids present, transforms are
    /// 16 finite numbers, every blob reference stays inside the export folder and exists.
    /// </summary>
    public static void Validate(Manifest manifest, string outputDirectory)
    {
        if (manifest.SchemaVersion != 1)
            throw Invalid($"Unsupported manifest schemaVersion {manifest.SchemaVersion}.");
        if (string.IsNullOrWhiteSpace(manifest.ExchangeId) || string.IsNullOrWhiteSpace(manifest.RevisionId))
            throw Invalid("Manifest needs exchangeId and revisionId.");
        if (manifest.Source is not ("sdk" or "mock"))
            throw Invalid("Manifest source must be 'sdk' or 'mock'.");

        if (manifest.ExchangeGeometry != null)
            CheckBlob(manifest.ExchangeGeometry, outputDirectory, "exchangeGeometry");

        foreach (var element in manifest.Elements)
        {
            if (string.IsNullOrWhiteSpace(element.SourceElementId))
                throw Invalid("Every element needs a sourceElementId.");
            if (element.Transform is { } t && (t.Length != 16 || t.Any(v => !double.IsFinite(v))))
                throw Invalid($"Element {element.SourceElementId}: transform must be 16 finite numbers.");
            if (element.Geometry != null)
                CheckBlob(element.Geometry, outputDirectory, $"element {element.SourceElementId}");
        }
    }

    private static void CheckBlob(ManifestGeometry geometry, string outputDirectory, string owner)
    {
        if (string.IsNullOrWhiteSpace(geometry.Kind))
            throw Invalid($"{owner}: geometry kind is missing.");
        if (geometry.BlobRef == null)
            return;

        var root = Path.GetFullPath(outputDirectory) + Path.DirectorySeparatorChar;
        var full = Path.GetFullPath(Path.Combine(outputDirectory, geometry.BlobRef));
        if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            throw Invalid($"{owner}: blobRef escapes the export folder.");
        if (!File.Exists(full) || new FileInfo(full).Length == 0)
            throw new HelperException(ErrorCodes.UnsupportedGeometry, $"{owner}: geometry file is missing or empty.");
    }

    private static HelperException Invalid(string message) => new(ErrorCodes.InternalError, message);
}

/// <summary>
/// 4x4 transforms in the manifest are ROW-MAJOR, translation in the last column (indices 3, 7, 11),
/// last row 0 0 0 1. That matches adsk.core.Matrix3D.setWithArray, so the add-in passes them through
/// after scaling translation into centimetres.
/// </summary>
public static class Transforms
{
    public static readonly double[] Identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

    /// <summary>Column-major (translation at 12, 13, 14) to the manifest's row-major layout.</summary>
    public static double[] FromColumnMajor(IReadOnlyList<double> m)
    {
        if (m.Count != 16)
            throw new ArgumentException("Expected 16 values.", nameof(m));
        var r = new double[16];
        for (var row = 0; row < 4; row++)
            for (var col = 0; col < 4; col++)
                r[row * 4 + col] = m[col * 4 + row];
        return r;
    }

    public static double[] Multiply(IReadOnlyList<double> a, IReadOnlyList<double> b)
    {
        var r = new double[16];
        for (var row = 0; row < 4; row++)
            for (var col = 0; col < 4; col++)
            {
                double sum = 0;
                for (var k = 0; k < 4; k++)
                    sum += a[row * 4 + k] * b[k * 4 + col];
                r[row * 4 + col] = sum;
            }
        return r;
    }

    public static (double X, double Y, double Z) Apply(IReadOnlyList<double> m, double x, double y, double z) =>
        (m[0] * x + m[1] * y + m[2] * z + m[3],
         m[4] * x + m[5] * y + m[6] * z + m[7],
         m[8] * x + m[9] * y + m[10] * z + m[11]);

    /// <summary>Scale factor from a named length unit to centimetres (Fusion's internal unit).</summary>
    public static double ToCentimetres(string unit) => unit.Trim().ToLowerInvariant() switch
    {
        "mm" or "millimeter" or "millimeters" or "millimetre" or "millimetres" => 0.1,
        "cm" or "centimeter" or "centimeters" or "centimetre" or "centimetres" => 1.0,
        "m" or "meter" or "meters" or "metre" or "metres" => 100.0,
        "in" or "inch" or "inches" => 2.54,
        "ft" or "foot" or "feet" => 30.48,
        _ => throw new HelperException(ErrorCodes.UnsupportedGeometry, $"Unknown length unit '{unit}'."),
    };
}
