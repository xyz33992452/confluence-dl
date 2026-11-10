# Folder download implementation

Accept Confluence Cloud `/wiki/spaces/SPACE/folder/ID` links in the existing web form and CLI. Enumerate folder contents via `/wiki/api/v2/folders/{id}/direct-children`, following pagination. Recurse through both folders and pages, preserving directory structure. Folder nodes must not be exported as documents or counted as downloaded pages. Reuse existing page exporters, bandwidth limit, skip-existing settings and progress events. Unsupported content types must be reported, never passed to page export. Authentication errors and incomplete traversal must remain visible.

Implementation steps:
1. Extend URL parsing and add typed hierarchy nodes without changing existing page behavior.
2. Add folder metadata and mixed direct-children API methods with pagination safeguards.
3. Traverse mixed trees; skip export for folder nodes but recurse into their output directory. Count only page nodes.
4. Update form labels and add regression coverage for folders, nested folders, pagination, depth limits and unsupported content.
5. Build release executable and verify the existing service on port 18770.

API reference: https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-children/
