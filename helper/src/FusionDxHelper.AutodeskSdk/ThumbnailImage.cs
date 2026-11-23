using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace FusionDxHelper.AutodeskSdk;

/// <summary>
/// Turns whatever the SDK hands back as ExchangeMetadata.ThumbnailsImage (typed object, undocumented)
/// into PNG bytes sized for the Load window. Returns null for anything that is not an image.
/// </summary>
internal static class ThumbnailImage
{
    public const int MaxWidth = 320;
    public const int MaxHeight = 220;

    public static byte[]? ToPng(object? image)
    {
        try
        {
            switch (image)
            {
                case null:
                    return null;
                case Image drawing:
                    return Encode(drawing);
                case byte[] bytes:
                    return FromBytes(bytes);
                case Stream stream:
                    using (var copy = new MemoryStream())
                    {
                        stream.CopyTo(copy);
                        return FromBytes(copy.ToArray());
                    }
                case string text:
                    return FromText(text);
                default:
                    return null;
            }
        }
        catch (Exception)
        {
            // A broken thumbnail must never break browsing.
            return null;
        }
    }

    private static byte[]? FromText(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return null;
        if (File.Exists(text))
            return FromBytes(File.ReadAllBytes(text));
        var comma = text.StartsWith("data:", StringComparison.OrdinalIgnoreCase) ? text.IndexOf(',') : -1;
        return FromBytes(Convert.FromBase64String(comma >= 0 ? text[(comma + 1)..] : text));
    }

    private static byte[]? FromBytes(byte[] bytes)
    {
        if (bytes.Length == 0)
            return null;
        using var stream = new MemoryStream(bytes);
        using var decoded = Image.FromStream(stream);
        return Encode(decoded);
    }

    private static byte[] Encode(Image source)
    {
        var scale = Math.Min(1.0, Math.Min((double)MaxWidth / source.Width, (double)MaxHeight / source.Height));
        var width = Math.Max(1, (int)Math.Round(source.Width * scale));
        var height = Math.Max(1, (int)Math.Round(source.Height * scale));
        using var scaled = new Bitmap(width, height, PixelFormat.Format32bppArgb);
        using (var graphics = Graphics.FromImage(scaled))
        {
            graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
            graphics.DrawImage(source, 0, 0, width, height);
        }
        using var output = new MemoryStream();
        scaled.Save(output, ImageFormat.Png);
        return output.ToArray();
    }
}
