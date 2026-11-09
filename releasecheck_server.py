import build_tools as bt
from mcp.server import MCPServer

server = MCPServer(
    "ReleaseCheck",
    instructions="Opens an app build you own and checks it against a plain-English release checklist. Read-only: nothing in the build is executed.",
)


@server.tool(description="Unpack a build (folder, .zip, .apk, .jar, ...) into a workspace and return its build_id.")
def open_build(path: str) -> dict:
    return bt.open_build(path)


@server.tool(description="List files inside an opened build with their size in KB.")
def list_files(build_id: str, limit: int = 200) -> list[dict]:
    return bt.list_files(build_id, limit)


@server.tool(description="Read lines from a text file inside the build. Lines are numbered.")
def read_file(build_id: str, path: str, start_line: int = 1, max_lines: int = 120) -> str:
    return bt.read_file(build_id, path, start_line, max_lines)


@server.tool(description="Find lines containing text (case-insensitive) across the build.")
def search_text(build_id: str, text: str, limit: int = 30) -> list[dict]:
    return bt.search_text(build_id, text, limit)


@server.tool(description="Return the release checklist: what is checked, why it matters and how to fix it.")
def get_checklist() -> list[dict]:
    return bt.load_checklist()


@server.tool(description="Run every checklist item over the build and return each match with file, line and evidence.")
def run_checklist(build_id: str) -> list[dict]:
    return bt.run_checklist(build_id)


@server.tool(description="Store a finished release report.")
def save_report(build_id: str, name: str, verdict: str, issues: int, report_md: str) -> dict:
    return bt.save_report(build_id, name, verdict, issues, report_md)


@server.tool(description="List recent release reports.")
def get_history(limit: int = 20) -> list[dict]:
    return bt.get_history(limit)


@server.resource("report://{report_id}", description="A saved release report in Markdown.")
def report(report_id: str) -> str:
    return bt.get_report(int(report_id))


@server.prompt(description="Prompt for writing a short release-readiness summary.")
def release_summary(app_name: str) -> str:
    return f"Write a short, friendly release-readiness summary for {app_name}. Use only the issues provided. Plain English, no jargon."


if __name__ == "__main__":
    server.run()
