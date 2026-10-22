"""Evidence-driven completion: turn verified facts into the finished harness.

Where `drive` wires transport for confirmed channels, `complete` generates the
domain surface: one command group per database table, one command per verified
endpoint, tests from observed response shapes, and a SKILL.md that documents
all of it. Deterministic generation from verified evidence - no LLM in the
loop, no guessing: if a fact was never verified, the command is not generated.

Safety rules baked into every generated command:
  - reads go through a read-only SQLite connection
  - writes require --yes AND take a timestamped backup first
  - identifiers are quoted; payloads are validated against the live schema
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .evidence import EvidenceBundle
from .surface import HarnessPlan

BACKEND_ADDITIONS = '''

# ---- domain surface added by auto-cli complete ----------------------

import re as _re


def _quote_ident(name: str) -> str:
    """SQLite identifier quoting. Rejects anything that is not a plain name."""
    if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise BackendError(f"unsafe identifier: {name!r}")
    return '"' + name + '"'


def table_columns(path: Path, table: str) -> list[dict]:
    schema = db_schema(path)
    if table not in schema:
        raise BackendError(f"unknown table: {table}")
    return schema[table]


def table_list(path: Path, table: str, limit: int = 100, where: str | None = None) -> dict:
    """Read-only listing with an optional caller-supplied WHERE fragment."""
    t = _quote_ident(table)
    sql = f"SELECT * FROM {t}"
    if where:
        if _re.search(r";|--|/\\*", where):
            raise BackendError("WHERE fragment contains forbidden characters")
        sql += f" WHERE {where}"
    sql += f" LIMIT {max(1, min(int(limit), 10000))}"
    return run_query(path, sql, limit=10_000)


def table_get(path: Path, table: str, rowid: int) -> dict:
    t = _quote_ident(table)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(f"SELECT * FROM {t} WHERE rowid = ?", (rowid,))]
    finally:
        con.close()
    if not rows:
        raise BackendError(f"no row {rowid} in {table}")
    return rows[0]


def table_search(path: Path, table: str, term: str, limit: int = 50) -> dict:
    t = _quote_ident(table)
    cols = [c["name"] for c in table_columns(path, table)]
    likes = " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in cols)
    sql = f"SELECT * FROM {t} WHERE {likes} LIMIT {max(1, min(int(limit), 1000))}"
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(sql, (f"%{term}%",) * len(cols))]
    finally:
        con.close()
    return {"table": table, "term": term, "matches": rows}


def _insert(path: Path, table: str, payload: dict, update: bool, rowid: int | None) -> dict:
    cols = {c["name"] for c in table_columns(path, table)}
    unknown = sorted(set(payload) - cols)
    if unknown:
        raise BackendError(f"unknown columns for {table}: {unknown}")
    t = _quote_ident(table)
    con = sqlite3.connect(path)
    try:
        if update:
            if not payload:
                raise BackendError("update needs at least one column")
            sets = ", ".join(f'"{c}" = ?' for c in payload)
            con.execute(f"UPDATE {t} SET {sets} WHERE rowid = ?", (*payload.values(), rowid))
            con.commit()
            return {"updated": rowid, "table": table}
        keys = ", ".join(f'"{c}"' for c in payload)
        marks = ", ".join("?" for _ in payload)
        cur = con.execute(f"INSERT INTO {t} ({keys}) VALUES ({marks})", tuple(payload.values()))
        con.commit()
        return {"inserted": cur.lastrowid, "table": table}
    finally:
        con.close()


def table_add(path: Path, table: str, payload: dict) -> dict:
    return _insert(path, table, payload, update=False, rowid=None)


def table_update(path: Path, table: str, rowid: int, payload: dict) -> dict:
    return _insert(path, table, payload, update=True, rowid=rowid)


def table_delete(path: Path, table: str, rowid: int) -> dict:
    t = _quote_ident(table)
    con = sqlite3.connect(path)
    try:
        cur = con.execute(f"DELETE FROM {t} WHERE rowid = ?", (rowid,))
        con.commit()
        return {"deleted": cur.rowcount, "table": table, "rowid": rowid}
    finally:
        con.close()
'''

GROUP_TEMPLATE = '''

@main.group()
@click.pass_context
def {group}(ctx: click.Context) -> None:
    """{table} table commands."""


@{group}.command("list")
@click.option("--limit", default=100)
@click.option("--where", default=None, help="SQL fragment, e.g. bpm > 120")
@click.argument("db", required=False)
@click.pass_context
def {group}_list(ctx: click.Context, limit: int, where: str | None, db: str | None) -> None:
    """Read-only listing of {table}."""
    try:
        _emit(ctx, backend.table_list(backend.resolve_db(db), "{table}", limit, where))
    except backend.BackendError as e:
        _fail(str(e))


@{group}.command("get")
@click.argument("rowid", type=int)
@click.argument("db", required=False)
@click.pass_context
def {group}_get(ctx: click.Context, rowid: int, db: str | None) -> None:
    """Read one {table} row by rowid."""
    try:
        _emit(ctx, backend.table_get(backend.resolve_db(db), "{table}", rowid))
    except backend.BackendError as e:
        _fail(str(e))


@{group}.command("search")
@click.argument("term")
@click.option("--limit", default=50)
@click.argument("db", required=False)
@click.pass_context
def {group}_search(ctx: click.Context, term: str, limit: int, db: str | None) -> None:
    """Search {table} across all columns."""
    try:
        _emit(ctx, backend.table_search(backend.resolve_db(db), "{table}", term, limit))
    except backend.BackendError as e:
        _fail(str(e))


@{group}.command("add")
@click.option("--payload", required=True, help="JSON object with column names as keys")
@click.option("--yes", is_flag=True, help="confirm the write (backup is taken first)")
@click.argument("db", required=False)
@click.pass_context
def {group}_add(ctx: click.Context, payload: str, yes: bool, db: str | None) -> None:
    """Insert into {table}. Requires --yes; takes a backup first."""
    if not yes:
        _fail("refusing to write without --yes (a backup is taken first)")
    try:
        path = backend.resolve_db(db)
        data = json.loads(payload)
        if not isinstance(data, dict):
            _fail("--payload must be a JSON object")
        out = backend.backup_db(path)
        _emit(ctx, {{"backup": str(out), **backend.table_add(path, "{table}", data)}})
    except backend.BackendError as e:
        _fail(str(e))
    except json.JSONDecodeError:
        _fail("--payload is not valid JSON")


@{group}.command("update")
@click.argument("rowid", type=int)
@click.option("--payload", required=True, help="JSON object of columns to change")
@click.option("--yes", is_flag=True, help="confirm the write (backup is taken first)")
@click.argument("db", required=False)
@click.pass_context
def {group}_update(ctx: click.Context, rowid: int, payload: str, yes: bool, db: str | None) -> None:
    """Update one {table} row. Requires --yes; takes a backup first."""
    if not yes:
        _fail("refusing to write without --yes (a backup is taken first)")
    try:
        path = backend.resolve_db(db)
        data = json.loads(payload)
        out = backend.backup_db(path)
        _emit(ctx, {{"backup": str(out), **backend.table_update(path, "{table}", rowid, data)}})
    except backend.BackendError as e:
        _fail(str(e))
    except json.JSONDecodeError:
        _fail("--payload is not valid JSON")


@{group}.command("delete")
@click.argument("rowid", type=int)
@click.option("--yes", is_flag=True, help="confirm the delete (backup is taken first)")
@click.argument("db", required=False)
@click.pass_context
def {group}_delete(ctx: click.Context, rowid: int, yes: bool, db: str | None) -> None:
    """Delete one {table} row. Requires --yes; takes a backup first."""
    if not yes:
        _fail("refusing to write without --yes (a backup is taken first)")
    try:
        path = backend.resolve_db(db)
        out = backend.backup_db(path)
        _emit(ctx, {{"backup": str(out), **backend.table_delete(path, "{table}", rowid)}})
    except backend.BackendError as e:
        _fail(str(e))
'''

ENDPOINT_TEMPLATE = '''

@main.command("{cmd}")
@click.option("--timeout", default=10.0)
@click.pass_context
def {fn}(ctx: click.Context, timeout: float) -> None:
    """GET {url} (verified live: {detail})."""
    import json as _json

    try:
        data = backend.http_get("{url}", timeout)
        if data["status"] != 200:
            _fail(f"endpoint returned HTTP {{data['status']}}")
        try:
            _emit(ctx, _json.loads(data["body"]))
        except _json.JSONDecodeError:
            _emit(ctx, {{"raw": data["body"][:2000]}})
    except backend.BackendError as e:
        _fail(str(e))
'''

# POST-style endpoints need auth + a body schema the forge cannot infer from
# a URL. Generate an honest stub instead of a wrong GET.
POST_STUB_TEMPLATE = '''

@main.command("{cmd}")
@click.option("--payload", default="{{}}", help="JSON request body")
@click.option("--timeout", default=10.0)
@click.pass_context
def {fn}(ctx: click.Context, payload: str, timeout: float) -> None:
    """POST {url} (documented: {detail}).

    NOT WIRED: this endpoint needs an Authorization header (token from the
    login command) and a request body. Wire both before enabling - the forge
    deliberately does not bake credentials into generated code.
    """
    import json as _json

    try:
        data = _json.loads(payload)
    except _json.JSONDecodeError:
        _fail("--payload must be valid JSON")
    _fail("not wired yet: add the auth header and body schema for {url}")
'''

DOMAIN_TESTS = '''

def test_{group}_list_reads_real_schema():
    """list/search/get run against the verified schema - no app needed."""
    import sqlite3
    from cli_anything.{module}.core import backend

    tmp = backend.resolve_db()
    con = sqlite3.connect(f"file:{{tmp}}?mode=ro", uri=True)
    tables = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    con.close()
    assert "{table}" in tables
    res = backend.table_list(tmp, "{table}", limit=5)
    assert "rows" in res


def test_{group}_write_requires_yes_and_backs_up(monkeypatch, tmp_path):
    """Writes are refused without --yes, and a backup precedes every write."""
    import json
    import shutil
    import sqlite3
    from click.testing import CliRunner
    from cli_anything.{module} import {module}_cli
    from cli_anything.{module}.core import backend

    db = tmp_path / "w.db"
    shutil.copy(backend.resolve_db(), db)
    monkeypatch.setattr(backend, "resolve_db", lambda override=None: db)
    backups = []
    monkeypatch.setattr(backend, "backup_db", lambda p, d=None: backups.append(str(p)) or p)

    # schema-aware payload: first writable column, whatever the table has
    cols = [c["name"] for c in backend.table_columns(db, "{table}") if not c["pk"]]
    if not cols:
        return
    col = cols[0]
    payload = json.dumps({{col: "forge-test"}})

    runner = CliRunner()
    res = runner.invoke({module}_cli.main, ["{group_dash}", "add", "--payload", payload])
    assert res.exit_code == 2  # no --yes -> refused

    res = runner.invoke(
        {module}_cli.main, ["{group_dash}", "add", "--payload", payload, "--yes"]
    )
    assert res.exit_code == 0, res.output
    assert backups, "write happened without a backup"
    con = sqlite3.connect(db)
    q = 'SELECT COUNT(*) FROM "' + "{table}" + '" WHERE "' + col + '" = ?'
    n = con.execute(q, ("forge-test",)).fetchone()[0]
    con.close()
    assert n == 1
'''

GENERIC_NAMES = {
    "info",
    "schema",
    "backup",
    "query",
    "export",
    "endpoints",
    "get",
    "channels",
    "send",
    "tracks",
    "tools",
    "call",
    "eval",
    "complete",
    "drive",
}


@dataclass
class CompletionReport:
    tables: list[str] = field(default_factory=list)
    endpoint_commands: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"table groups: {', '.join(self.tables) or '(none)'}"]
        out.append(f"endpoint commands: {', '.join(self.endpoint_commands) or '(none)'}")
        if self.skipped:
            out.append("skipped: " + ", ".join(self.skipped))
        return out


def _db_tables(target: str, default_db: str) -> list[str]:
    p = Path(target) / default_db if default_db else None
    if not p or not p.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        con.close()
        return tables
    except sqlite3.Error:
        return []


NOISE_SEGMENTS = {"xrpc", "api", "v1", "v2", "v3", "rest", "json", "http", "https"}


def _endpoint_command_name(url: str) -> str:
    from urllib.parse import urlsplit

    path = urlsplit(url).path
    parts = [p for p in path.split("/") if p and p.lower() not in NOISE_SEGMENTS]
    name = "-".join(parts) or "root"
    name = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    return name or "root"


def complete(
    bundle: EvidenceBundle,
    plan: HarnessPlan,
    scaffold_dir: str | Path,
    verification: dict | None = None,
) -> CompletionReport:
    """Generate the domain surface from verified evidence."""
    root = Path(scaffold_dir)
    if root.name != "agent-harness":
        found = next(root.rglob("agent-harness"), None)
        if found:
            root = found
    pkg = next(root.glob("cli_anything/*"), None)
    if not pkg:
        return CompletionReport(skipped=["no package found"])
    module = pkg.name
    report = CompletionReport()

    # ---- database domain surface
    backend_file = pkg / "core" / "backend.py"
    src = backend_file.read_text(encoding="utf-8")
    if "domain surface added by auto-cli complete" not in src:
        src += BACKEND_ADDITIONS
        backend_file.write_text(src, encoding="utf-8")

    default_db = ""
    m = re.search(r"DEFAULT_DB = ['\"]([^'\"]*)['\"]", src)
    if m:
        default_db = m.group(1)
    tables = _db_tables(plan.target, default_db)

    cli_file = pkg / f"{module}_cli.py"
    cli_src = cli_file.read_text(encoding="utf-8")

    for table in tables:
        group = table if re.fullmatch(r"[a-z][a-z0-9_]*", table) else f"{table}_rows"
        # only a REAL collision with the generated CLI matters
        if f"def {group}(" in cli_src or f"def {group}_" in cli_src:
            report.skipped.append(f"table {table} (name collision with {group})")
            continue
        if f"def {group}_list" in cli_src:
            continue
        cli_src = cli_src.replace(
            '\n\nif __name__ == "__main__":',
            GROUP_TEMPLATE.format(group=group, table=table) + '\n\nif __name__ == "__main__":',
        )
        report.tables.append(table)

        # per-table tests
        tests_file = pkg / "tests" / "test_core.py"
        tsrc = tests_file.read_text(encoding="utf-8")
        tsrc += DOMAIN_TESTS.format(group=group, group_dash=group.replace("_", "-"), table=table, module=module)
        tests_file.write_text(tsrc, encoding="utf-8")

    # ---- endpoint domain surface (verified endpoints only)
    verified = []
    if verification:
        for c in verification.get("checks", []):
            if c.get("kind") == "endpoint" and c.get("state") == "verified":
                verified.append((c["target"], c.get("detail", "")))
    # evidence may carry the intended command name: note "cmd:post"
    intent = {}
    for f in bundle.of_kind("endpoint"):
        for n in f.notes:
            if n.startswith("cmd:"):
                intent[f.value] = n[4:].strip()
    for url, detail in verified:
        cmd = intent.get(url) or _endpoint_command_name(url)
        fn = re.sub(r"[^a-z0-9_]", "_", cmd)
        if f"def {fn}(" in cli_src or f"def {fn}_" in cli_src:
            report.skipped.append(f"endpoint {url} (name collision)")
            continue
        # a documented POST gets an honest stub, never a wrong GET
        src_finding = next((f for f in bundle.of_kind("endpoint") if f.value == url), None)
        is_post = src_finding is not None and any(
            "POST" in n.upper() for n in src_finding.notes
        )
        template = POST_STUB_TEMPLATE if is_post else ENDPOINT_TEMPLATE
        cli_src = cli_src.replace(
            '\n\nif __name__ == "__main__":',
            template.format(cmd=cmd, fn=fn, url=url, detail=detail[:60])
            + '\n\nif __name__ == "__main__":',
        )
        if is_post:
            report.skipped.append(f"{cmd} (POST - needs auth+body wiring, stubbed)")
        report.endpoint_commands.append(cmd)

    cli_file.write_text(cli_src, encoding="utf-8")

    # ---- SKILL.md: document the generated surface
    _update_skill(pkg, plan, report)

    return report


def _update_skill(pkg: Path, plan: HarnessPlan, report: CompletionReport) -> None:
    skill = pkg / "skills" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    cli_name = "cli-anything-" + pkg.name.replace("_", "-")
    lines = ["", "## Generated domain surface", ""]
    for t in report.tables:
        tcmd = t.replace("_", "-")  # click renders underscores as dashes
        lines.append(f"### {t}")
        lines.append("```bash")
        lines.append(f"{cli_name} {tcmd} list [--limit N] [--where 'bpm > 120']")
        lines.append(f"{cli_name} {tcmd} get <rowid>")
        lines.append(f"{cli_name} {tcmd} search <term>")
        lines.append(f"{cli_name} {tcmd} add --payload '{{...}}' --yes   # backup taken first")
        lines.append(f"{cli_name} {tcmd} update <rowid> --payload '{{...}}' --yes")
        lines.append(f"{cli_name} {tcmd} delete <rowid> --yes")
        lines.append("```")
    for c in report.endpoint_commands:
        lines.append(f"- `{c}` - GET the verified endpoint (parsed JSON)")
    lines += [
        "",
        "Writes require `--yes` and take a timestamped backup first. Reads use a",
        "read-only connection. Nothing here was guessed - every command comes",
        "from evidence verified against the live app.",
        "",
    ]
    if "## Generated domain surface" not in text:
        text = text.replace("## Rules for agents", "\n".join(lines) + "\n## Rules for agents")
    skill.write_text(text, encoding="utf-8")
