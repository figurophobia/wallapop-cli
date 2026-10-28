# PyInstaller spec: single-file executables for the MCP server and the CLI.
#
#   python scripts/build_binary.py     (builds into dist/bin/ and smoke-tests)
#
# Produces ``wallapop-mcp`` (stdio MCP server) and ``wallapop`` (CLI).
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

HERE = SPECPATH  # noqa: F821 (injected by PyInstaller)

# The mcp SDK imports parts of itself lazily; bundle it whole except its
# own ``mcp`` developer CLI (needs typer, unused here).
hiddenimports = collect_submodules("wallapop_mcp") + collect_submodules(
    "mcp", filter=lambda name: not name.startswith("mcp.cli")
)
datas = copy_metadata("mcp")
excludes = ["playwright", "tkinter", "pytest"]


def build(entry, name):
    a = Analysis(  # noqa: F821
        [f"{HERE}/{entry}"],
        pathex=[f"{HERE}/.."],
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=excludes,
        noarchive=False,
    )
    pyz = PYZ(a.pure)  # noqa: F821
    return EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=name,
        console=True,
        strip=False,
        upx=False,
    )


build("entry_mcp.py", "wallapop-mcp")
build("entry_cli.py", "wallapop")
