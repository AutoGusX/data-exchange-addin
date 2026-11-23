using FusionDxHelper.Core;

namespace FusionDxHelper.Tests;

public sealed class TransformTests
{
    [Fact]
    public void ColumnMajorTranslationMovesToLastColumn()
    {
        double[] columnMajor = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 10, 20, 30, 1];
        var rowMajor = Transforms.FromColumnMajor(columnMajor);
        Assert.Equal(10, rowMajor[3]);
        Assert.Equal(20, rowMajor[7]);
        Assert.Equal(30, rowMajor[11]);
        Assert.Equal((11d, 22d, 33d), Transforms.Apply(rowMajor, 1, 2, 3));
    }

    [Fact]
    public void ComposeAppliesRightHandOperandFirst()
    {
        double[] translate = [1, 0, 0, 5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
        double[] rotateZ90 = [0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

        var (x, y, _) = Transforms.Apply(Transforms.Multiply(translate, rotateZ90), 1, 0, 0);
        Assert.Equal(5, x, 9);
        Assert.Equal(1, y, 9);

        (x, y, _) = Transforms.Apply(Transforms.Multiply(rotateZ90, translate), 1, 0, 0);
        Assert.Equal(0, x, 9);
        Assert.Equal(6, y, 9);
    }

    [Theory]
    [InlineData("mm", 0.1)]
    [InlineData("Meters", 100)]
    [InlineData("feet", 30.48)]
    [InlineData("in", 2.54)]
    public void UnitsConvertToCentimetres(string unit, double expected) =>
        Assert.Equal(expected, Transforms.ToCentimetres(unit), 9);

    [Fact]
    public void UnknownUnitIsRejectedNotGuessed() =>
        Assert.Equal(ErrorCodes.UnsupportedGeometry,
            Assert.Throws<HelperException>(() => Transforms.ToCentimetres("cubits")).Code);
}

public sealed class ManifestTests : IDisposable
{
    private readonly string dir = Directory.CreateTempSubdirectory("dxtest").FullName;

    public void Dispose() => Directory.Delete(dir, true);

    private static Manifest Sample(ManifestGeometry? geometry = null, double[]? transform = null) => new()
    {
        ExchangeId = "ex", CollectionId = "col", HubId = "hub", RevisionId = "rev", Source = "mock",
        ExchangeGeometry = geometry,
        Elements = [new ManifestElement("el-1", "u-1", "Wall", "Walls", true, transform, null, null)],
    };

    [Fact]
    public void RoundTripsThroughDisk()
    {
        File.WriteAllText(Path.Combine(dir, "a.step"), "x");
        var path = ManifestIO.Write(Sample(new ManifestGeometry("step", "a.step"), Transforms.Identity), dir);
        var read = ManifestIO.Read(path);
        Assert.Equal("rev", read.RevisionId);
        Assert.Equal(16, read.Elements[0].Transform!.Length);
        Assert.Contains("\"schemaVersion\": 1", File.ReadAllText(path));
    }

    [Fact]
    public void RejectsBlobOutsideExportFolder() =>
        Assert.Throws<HelperException>(() => ManifestIO.Validate(Sample(new ManifestGeometry("step", "..\\evil.step")), dir));

    [Fact]
    public void RejectsMissingOrEmptyGeometry()
    {
        var ex = Assert.Throws<HelperException>(() => ManifestIO.Validate(Sample(new ManifestGeometry("step", "gone.step")), dir));
        Assert.Equal(ErrorCodes.UnsupportedGeometry, ex.Code);
        File.WriteAllText(Path.Combine(dir, "empty.step"), "");
        Assert.Throws<HelperException>(() => ManifestIO.Validate(Sample(new ManifestGeometry("step", "empty.step")), dir));
    }

    [Fact]
    public void RejectsBadTransform()
    {
        Assert.Throws<HelperException>(() => ManifestIO.Validate(Sample(transform: [1, 2, 3]), dir));
        var nan = (double[])Transforms.Identity.Clone();
        nan[3] = double.NaN;
        Assert.Throws<HelperException>(() => ManifestIO.Validate(Sample(transform: nan), dir));
    }
}
