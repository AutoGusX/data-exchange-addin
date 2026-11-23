using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using FusionDxHelper.Core;

namespace FusionDxHelper.AutodeskSdk;

/// <summary>
/// Reads the element data of an AEC Data Model based exchange through the AEC Data Model API
/// (GraphQL, https://aps.autodesk.com/en/docs/aecdatamodel/v1/). The Data Exchange SDK cannot read
/// these exchanges; this is a plain HTTPS call with the signed-in user's token. Read-only.
/// </summary>
internal static class AecDataModelReader
{
    private const string Endpoint = "https://developer.api.autodesk.com/aec/graphql";
    private const int PageSize = 50;
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(60) };

    private const string ProjectQuery =
        "query($id: ID!) { projectByDataManagementAPIId(dataManagementAPIProjectId: $id) { id name } }";

    private const string GroupQuery =
        "query($projectId: ID!, $filter: ElementGroupFilterInput) { elementGroupsByProject(projectId: $projectId, filter: $filter) " +
        "{ results { id name alternativeIdentifiers { fileUrn } } } }";

    private const string ElementsQuery =
        "query($groupId: ID!, $pagination: PaginationInput) { elementsByElementGroup(elementGroupId: $groupId, pagination: $pagination) " +
        "{ totalCount pagination { cursor } results { id name properties { results { name value displayValue } } } } }";

    public static async Task<DataOnlyElements> ReadAsync(
        string token, string? region, string projectId, string fileUrn, int maxElements, CancellationToken ct)
    {
        var lineage = fileUrn.Split('?')[0];
        var project = await QueryAsync(token, region, ProjectQuery, new { id = projectId }, ct).ConfigureAwait(false);
        var aecProjectId = Text(project, "projectByDataManagementAPIId", "id")
            ?? throw new HelperException(ErrorCodes.NotFound,
                "The AEC Data Model API does not know this project. AEC Data Model may not be activated for the hub.");

        var groups = await QueryAsync(token, region, GroupQuery,
            new { projectId = aecProjectId, filter = new { fileUrn = new[] { lineage } } }, ct).ConfigureAwait(false);
        JsonElement? group = null;
        if (groups.TryGetProperty("elementGroupsByProject", out var found) && found.ValueKind == JsonValueKind.Object
            && found.TryGetProperty("results", out var results) && results.ValueKind == JsonValueKind.Array)
            foreach (var item in results.EnumerateArray())
            {
                group = item;
                break;
            }
        if (group == null)
            throw new HelperException(ErrorCodes.NotFound,
                "The AEC Data Model API lists no data for this exchange.");
        var groupId = group.Value.GetProperty("id").GetString()!;
        var groupName = group.Value.TryGetProperty("name", out var n) ? n.GetString() : null;

        var listed = new List<DataElement>();
        var total = 0;
        string? cursor = null;
        do
        {
            var page = await QueryAsync(token, region, ElementsQuery,
                new { groupId, pagination = new { limit = PageSize, cursor } }, ct).ConfigureAwait(false);
            if (!page.TryGetProperty("elementsByElementGroup", out var elements) || elements.ValueKind != JsonValueKind.Object)
                break;
            if (elements.TryGetProperty("totalCount", out var count) && count.ValueKind == JsonValueKind.Number)
                total = count.GetInt32();
            foreach (var element in elements.GetProperty("results").EnumerateArray())
            {
                if (listed.Count >= maxElements)
                    break;
                listed.Add(ToElement(element));
            }
            cursor = elements.TryGetProperty("pagination", out var paging) && paging.ValueKind == JsonValueKind.Object
                && paging.TryGetProperty("cursor", out var next) ? next.GetString() : null;
        }
        while (!string.IsNullOrEmpty(cursor) && listed.Count < maxElements);

        return new DataOnlyElements(groupName, Math.Max(total, listed.Count), listed, "aecdm");
    }

    private static DataElement ToElement(JsonElement element)
    {
        var name = element.TryGetProperty("name", out var n) ? n.GetString() : null;
        string? category = null;
        var properties = new Dictionary<string, string>(StringComparer.Ordinal);
        if (element.TryGetProperty("properties", out var holder) && holder.ValueKind == JsonValueKind.Object
            && holder.TryGetProperty("results", out var list) && list.ValueKind == JsonValueKind.Array)
            foreach (var property in list.EnumerateArray())
            {
                var key = property.TryGetProperty("name", out var k) ? k.GetString() : null;
                var value = Scalar(property, "displayValue") ?? Scalar(property, "value");
                if (string.IsNullOrWhiteSpace(key) || string.IsNullOrWhiteSpace(value))
                    continue;
                if (key is "Revit Category Type Id" or "Category" or "category")
                    category ??= value;
                if (properties.Count < DataElement.MaxProperties)
                    properties.TryAdd(key, value.Length > DataElement.MaxValueLength ? value[..DataElement.MaxValueLength] : value);
            }
        return new DataElement(string.IsNullOrWhiteSpace(name) ? "(unnamed)" : name!, category,
            properties.Count == 0 ? null : properties);
    }

    private static string? Scalar(JsonElement parent, string name)
    {
        if (!parent.TryGetProperty(name, out var value))
            return null;
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString(),
            JsonValueKind.Null or JsonValueKind.Undefined => null,
            _ => value.GetRawText(),
        };
    }

    private static string? Text(JsonElement data, string field, string child) =>
        data.TryGetProperty(field, out var holder) && holder.ValueKind == JsonValueKind.Object
        && holder.TryGetProperty(child, out var value) ? value.GetString() : null;

    /// <summary>Posts one query and returns its "data" object. GraphQL errors become helper errors.</summary>
    private static async Task<JsonElement> QueryAsync(string token, string? region, string query, object variables, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, Endpoint);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        if (!string.IsNullOrWhiteSpace(region))
            request.Headers.TryAddWithoutValidation("Region", region.Trim().ToUpperInvariant());
        request.Content = new StringContent(JsonSerializer.Serialize(new { query, variables }), Encoding.UTF8, "application/json");

        HttpResponseMessage response;
        try
        {
            response = await Http.SendAsync(request, ct).ConfigureAwait(false);
        }
        catch (HttpRequestException ex)
        {
            throw new HelperException(ErrorCodes.NetworkError, "Could not reach the AEC Data Model API.", ex);
        }
        using (response)
        {
            var body = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
            if ((int)response.StatusCode == 401)
                throw new HelperException(ErrorCodes.AuthRequired, "Sign in again.");
            if ((int)response.StatusCode == 403)
                throw new HelperException(ErrorCodes.Forbidden, "No access to this data through the AEC Data Model API.");
            JsonElement root;
            try
            {
                root = JsonDocument.Parse(body).RootElement.Clone();
            }
            catch (JsonException)
            {
                throw new HelperException(ErrorCodes.InternalError,
                    $"The AEC Data Model API answered HTTP {(int)response.StatusCode} with no readable body.");
            }
            if (root.TryGetProperty("errors", out var errors) && errors.ValueKind == JsonValueKind.Array && errors.GetArrayLength() > 0)
            {
                var first = errors[0];
                var message = first.TryGetProperty("message", out var m) ? m.GetString() : "Unknown error";
                var missing = message != null && (message.Contains("not found", StringComparison.OrdinalIgnoreCase)
                    || message.Contains("does not exist", StringComparison.OrdinalIgnoreCase));
                throw new HelperException(missing ? ErrorCodes.NotFound : ErrorCodes.InternalError,
                    "AEC Data Model API: " + message);
            }
            if (!response.IsSuccessStatusCode || !root.TryGetProperty("data", out var data) || data.ValueKind != JsonValueKind.Object)
                throw new HelperException(ErrorCodes.InternalError,
                    $"The AEC Data Model API answered HTTP {(int)response.StatusCode} with no data.");
            return data;
        }
    }
}
