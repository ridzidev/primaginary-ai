import asyncio
from pathlib import Path
from typing import Dict, Any, List
import config

def resolve_path(rel_path: str) -> Path:
    clean = rel_path.strip().replace("\\", "/").lstrip("/")
    if not clean:
        raise ValueError("Path tidak boleh kosong.")
    target = (config.WORKSPACE / clean).resolve()
    if target != config.WORKSPACE and config.WORKSPACE not in target.parents:
        raise ValueError(f"Akses di luar workspace dilarang: {rel_path}")
    return target

def list_dir(rel_path: str = ".") -> str:
    try:
        base = resolve_path(rel_path)
    except Exception as e:
        return f"Error: {e}"
    if not base.exists():
        return f"Error: Direktori '{rel_path}' tidak ditemukan."
    if not base.is_dir():
        return f"Error: '{rel_path}' adalah file, bukan direktori."

    items: List[str] = []
    for item in sorted(base.rglob("*")):
        if any(part in config.SKIP_DIRS or part.startswith(".") for part in item.parts):
            continue
        rel = item.relative_to(config.WORKSPACE).as_posix()
        items.append(rel + ("/" if item.is_dir() else ""))
        if len(items) >= 150:
            items.append("... [output dipotong]")
            break
    return "\n".join(items) if items else "(direktori kosong)"

def read_file(rel_path: str, start_line: int = 1, end_line: int = 150) -> str:
    try:
        target = resolve_path(rel_path)
    except Exception as e:
        return f"Error: {e}"
    if not target.is_file():
        return f"Error: File '{rel_path}' tidak ditemukan."

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Gagal membaca file: {e}"

    lines = content.splitlines()
    total = len(lines)
    s_idx = max(1, start_line) - 1
    e_idx = min(total, end_line)

    if s_idx >= total:
        return f"File '{rel_path}' hanya memiliki {total} baris. start_line {start_line} melebihi batas."

    selected = lines[s_idx:e_idx]
    numbered = [f"{i + 1:4d} | {line}" for i, line in enumerate(selected, start=s_idx)]
    header = f"=== File: {rel_path} (Baris {s_idx + 1}-{e_idx} dari total {total}) ===\n"
    return header + "\n".join(numbered)

def write_file(rel_path: str, content: str) -> str:
    try:
        target = resolve_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Berhasil menulis {rel_path} ({len(content)} karakter)."
    except Exception as e:
        return f"Gagal menulis file: {e}"

def patch_file(rel_path: str, search_block: str, replace_block: str) -> str:
    try:
        target = resolve_path(rel_path)
    except Exception as e:
        return f"Error: {e}"
    if not target.is_file():
        return f"Error: File '{rel_path}' tidak ditemukan untuk di-patch."

    content = target.read_text(encoding="utf-8", errors="replace")
    clean_search = search_block.replace("\r\n", "\n")
    clean_replace = replace_block.replace("\r\n", "\n")
    clean_content = content.replace("\r\n", "\n")

    count = clean_content.count(clean_search)
    if count == 1:
        new_content = clean_content.replace(clean_search, clean_replace, 1)
        target.write_text(new_content, encoding="utf-8")
        return f"Sukses: Patch diterapkan pada {rel_path} (Exact match)."
    if count > 1:
        return (f"Gagal: Blok search ditemukan {count} kali di '{rel_path}'. "
                "Tambahkan baris penanda konteks di sekitarnya agar unik.")

    search_lines = [l.strip() for l in clean_search.splitlines() if l.strip()]
    file_lines = clean_content.splitlines()

    match_idx = -1
    for i in range(len(file_lines) - len(search_lines) + 1):
        window = [file_lines[i + j].strip() for j in range(len(search_lines))]
        if window == search_lines:
            if match_idx != -1:
                return "Gagal: Blok search fuzzy cocok di lebih dari satu lokasi."
            match_idx = i

    if match_idx != -1:
        before = file_lines[:match_idx]
        after = file_lines[match_idx + len(search_lines):]
        new_lines = before + clean_replace.splitlines() + after
        target.write_text("\n".join(new_lines), encoding="utf-8")
        return f"Sukses: Patch diterapkan pada {rel_path} (Fuzzy whitespace match)."

    return ("Gagal: Blok search tidak ditemukan. "
            "Gunakan 'read_file' untuk memastikan baris konteks sebelum patch.")

async def run_command(command: str) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(config.WORKSPACE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=config.COMMAND_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"Timeout: Command melebihi batas waktu {config.COMMAND_TIMEOUT} detik."

        out = stdout_bytes.decode("utf-8", errors="replace").strip()
        err = stderr_bytes.decode("utf-8", errors="replace").strip()

        result = []
        if out:
            result.append(f"[STDOUT]\n{out}")
        if err:
            result.append(f"[STDERR]\n{err}")
        result.append(f"[EXIT CODE]: {proc.returncode}")

        merged = "\n\n".join(result)
        if len(merged) > config.MAX_COMMAND_OUTPUT_CHARS:
            return merged[:config.MAX_COMMAND_OUTPUT_CHARS] + "\n... [output dipotong]"
        return merged or "(selesai tanpa output)"
    except Exception as e:
        return f"Gagal mengeksekusi terminal command: {e}"

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories in current workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rel_path": {"type": "string", "description": "Relative path (default '.')"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file lines with numbering to inspect context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rel_path": {"type": "string", "description": "Relative path to file"},
                    "start_line": {"type": "integer", "description": "Start line (1-based)"},
                    "end_line": {"type": "integer", "description": "End line (1-based)"}
                },
                "required": ["rel_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": "Apply search and replace block to modify an existing file without rewriting it fully.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rel_path": {"type": "string", "description": "Target file path"},
                    "search_block": {"type": "string", "description": "Exact existing code snippet to locate"},
                    "replace_block": {"type": "string", "description": "New replacement code snippet"}
                },
                "required": ["rel_path", "search_block", "replace_block"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or completely rewrite an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rel_path": {"type": "string", "description": "File path to create or rewrite"},
                    "content": {"type": "string", "description": "Full file content"}
                },
                "required": ["rel_path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run shell command in workspace (git, pytest, npm, build, etc.) to inspect and verify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Bash command to execute"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Signal that the task is fully completed and verified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Summary of changes and verification"}
                },
                "required": ["summary"]
            }
        }
    }
]

async def execute_tool(name: str, args: Dict[str, Any]) -> str:
    if name == "list_dir":
        return list_dir(args.get("rel_path", "."))
    elif name == "read_file":
        return read_file(
            args.get("rel_path", ""),
            int(args.get("start_line", 1)),
            int(args.get("end_line", 150))
        )
    elif name == "patch_file":
        return patch_file(
            args.get("rel_path", ""),
            args.get("search_block", ""),
            args.get("replace_block", "")
        )
    elif name == "write_file":
        return write_file(args.get("rel_path", ""), args.get("content", ""))
    elif name == "run_command":
        return await run_command(args.get("command", ""))
    return f"Error: Tool '{name}' tidak dikenal."
