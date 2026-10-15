# Run inside the agent-server image:
# docker run --rm -e OPENHANDS_SUPPRESS_BANNER=1 -v $PWD/nova_server_patches:/opt/nova/tools/nova_server_patches:ro \
#   -v $PWD/sidecar_bridge:/opt/nova/tools/sidecar_bridge:ro -v $PWD/tests/client_tool_versions_check.py:/t.py:ro \
#   --entrypoint python3 <image> /t.py
# Owner 2026-10-15: a UI release (canvas_ui_control gained open_url) made every new conversation 422 until the
# workspace restarted. Both schemas must now register in one process, and each conversation keeps its own fields.
import sys
sys.path.insert(0, "/opt/nova/tools")
import nova_server_patches  # noqa: F401  (installs the patches)
from openhands.sdk.tool.client_tool import ClientToolSpec, register_client_tools, ClientTool

old = {"type": "object", "properties": {"command": {"type": "string", "enum": ["navigate_to_file", "open_tab"]},
       "path": {"type": "string"}}, "required": ["command"]}
new = {"type": "object", "properties": {"command": {"type": "string", "enum": ["navigate_to_file", "open_tab", "open_url"]},
       "path": {"type": "string"}, "url": {"type": "string"}}, "required": ["command"]}
spec = lambda p: ClientToolSpec(name="canvas_ui_control", description="canvas", parameters=p)

register_client_tools([spec(old)])
register_client_tools([spec(new)])              # stock SDK: ClientToolSchemaConflictError -> 422
register_client_tools([spec(old)])              # an old client again: still fine
t_old = ClientTool.from_spec(spec(old)); t_new = ClientTool.from_spec(spec(new))
assert t_old.action_type is not t_new.action_type
assert "url" in t_new.action_type.model_fields and "url" not in t_old.action_type.model_fields
assert t_old.action_type.__name__ == "ClientAction_canvas_ui_control"
assert ClientTool.from_spec(spec(new)).action_type is t_new.action_type    # cached, no duplicate class
a = t_new.action_type.model_validate({"command": "open_url", "url": "http://intranet"})
print("new schema action:", type(a).__name__, a.url)
print("ALL OK")
