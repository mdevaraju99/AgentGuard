from __future__ import annotations

from collections.abc import Iterable

from desktop_agent.tools.base import Tool


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def openai_tools(self) -> list[dict]:
        return [tool.json_schema() for tool in self._tools.values()]

    def descriptions(self) -> str:
        lines = []
        for tool in self._tools.values():
            lines.append(f"- {tool.name} [{tool.risk_level.value}]: {tool.description}")
        return "\n".join(lines)


def build_default_registry() -> ToolRegistry:
    from desktop_agent.tools.applications import build_application_tools
    from desktop_agent.tools.browser import build_browser_tools
    from desktop_agent.tools.documents import build_document_tools
    from desktop_agent.tools.files import build_file_tools
    from desktop_agent.tools.installed import build_installed_tools
    from desktop_agent.tools.controls import build_control_tools
    from desktop_agent.tools.inventory import build_inventory_tools
    from desktop_agent.tools.system import build_system_tools
    from desktop_agent.tools.time_tools import build_time_tools

    registry = ToolRegistry()
    for builder in (
        build_application_tools,
        build_file_tools,
        build_document_tools,
        build_browser_tools,
        build_time_tools,
        build_installed_tools,
        build_system_tools,
        build_inventory_tools,
        build_control_tools,
    ):
        for tool in builder():
            registry.register(tool)
    return registry
