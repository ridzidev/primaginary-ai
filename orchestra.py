import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
import config
from llm import LLMClient
import tools

SESSIONS_DIR = config.GLOBAL_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

def get_session_file() -> Path:
    # Hash path workspace agar tiap folder proyek punya file sesi masing-masing
    ws_hash = hashlib.md5(str(config.WORKSPACE).encode()).hexdigest()[:12]
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', config.WORKSPACE.name)
    return SESSIONS_DIR / f"{safe_name}_{ws_hash}.json"

def save_session(messages: List[Dict[str, Any]]):
    try:
        session_file = get_session_file()
        session_file.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def load_session() -> Optional[List[Dict[str, Any]]]:
    session_file = get_session_file()
    if session_file.is_file():
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) > 0:
                return data
        except Exception:
            pass
    return None

def compress_history(messages: List[Dict[str, Any]], level: int = 1) -> List[Dict[str, Any]]:
    compressed = []
    total_msgs = len(messages)

    for i, msg in enumerate(messages):
        if i <= 1:
            compressed.append(msg)
            continue

        role = msg.get("role")
        content = msg.get("content") or ""

        if role == "tool":
            is_latest_tool = (i >= total_msgs - 2)
            if not is_latest_tool or level >= 2:
                if len(content) > 300:
                    first_line = content.splitlines()[0] if content.splitlines() else "output"
                    msg = {
                        **msg,
                        "content": f"[KONTEKS LAMA DIPANGKAS] {first_line[:120]}... (sudah diproses di langkah sebelumnya)"
                    }
            if level >= 3 and len(content) > 600:
                msg = {
                    **msg,
                    "content": content[:500] + "\n... [dipangkas dinamis karena batas kuota token]"
                }
        elif role == "assistant" and len(content) > 600 and i < total_msgs - 3:
            if level >= 2:
                msg = {
                    **msg,
                    "content": content[:400] + "\n... [diringkas]"
                }

        compressed.append(msg)

    return compressed

async def select_model_interactive() -> bool:
    llm = LLMClient()
    print(f"\033[1;36m🔄 Menghubungi {config.API_BASE_URL}/models...\033[0m")
    models = await llm.get_available_models()
    if not models:
        print("\033[1;31m❌ Gagal mengambil daftar model.\033[0m")
        return False

    print("\n\033[1;36m=== DAFTAR MODEL AKTIF DARI PROVIDER ===\033[0m")
    for i, m in enumerate(models, 1):
        active_mark = " \033[1;32m(aktif)\033[0m" if m == config.MODEL_NAME else ""
        print(f"[{i:2d}] {m}{active_mark}")

    print("")
    try:
        pilihan = input(f"Pilih nomor model (1-{len(models)}): ").strip()
        idx = int(pilihan) - 1
        if idx < 0 or idx >= len(models):
            print("Pilihan tidak valid.")
            return False
        chosen = models[idx]
    except (ValueError, KeyboardInterrupt):
        print("\nDibatalkan.")
        return False

    env_path = config.GLOBAL_DIR / ".env"
    content = env_path.read_text(encoding="utf-8")
    if "MODEL_NAME=" in content:
        content = re.sub(r'MODEL_NAME=.*', f'MODEL_NAME="{chosen}"', content)
    else:
        content += f'\nMODEL_NAME="{chosen}"'
    env_path.write_text(content, encoding="utf-8")
    config.MODEL_NAME = chosen

    print(f"\033[1;32m✅ Model berhasil disetel ke:\033[0m \033[1m{chosen}\033[0m\n")
    return True

async def execute_task_loop(task: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    llm = LLMClient()
    messages.append({"role": "user", "content": task})

    for turn in range(1, config.MAX_AGENT_TURNS + 1):
        print(f"\033[1;34m--- [Turn {turn}/{config.MAX_AGENT_TURNS}] ---\033[0m")
        messages = compress_history(messages, level=1)

        msg = None
        for reduce_level in range(1, 4):
            try:
                msg = await llm.call(messages, tools=tools.TOOLS_SCHEMA)
                break
            except LookupError as e:
                print(f"\n\033[1;33m⚠️ {e}\033[0m")
                if await select_model_interactive():
                    return await execute_task_loop(task, messages)
                return messages
            except RuntimeError as e:
                err_str = str(e)
                if "413" in err_str or "ITPM" in err_str or "too large" in err_str.lower():
                    print(f"\n\033[1;33m⚠️ [PAYLOAD BESAR / ITPM LIMIT] Menciutkan riwayat konteks (Level {reduce_level})...\033[0m")
                    messages = compress_history(messages, level=reduce_level + 1)
                    await asyncio.sleep(1.0)
                    continue
                else:
                    print(f"\033[1;31m[ERROR LLM]: {e}\033[0m")
                    return messages

        if not msg:
            print("\033[1;31m[GAGAL]: Konteks sudah diciutkan maksimal namun tetap melebihi limit provider.\033[0m")
            return messages

        messages.append(msg)
        tool_calls = msg.get("tool_calls")
        content = msg.get("content")

        if content and content.strip():
            print(f"\033[0;37m{content}\033[0m")

        if not tool_calls:
            messages.append({
                "role": "user",
                "content": "Lanjutkan tugas dengan memanggil tool yang sesuai atau panggil 'finish' jika selesai."
            })
            continue

        for tc in tool_calls:
            call_id = tc["id"]
            fn_name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]

            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                args = {}

            print(f"\033[1;33m⚙️  Tool Call\033[0m: \033[1m{fn_name}\033[0m")
            if fn_name == "run_command":
                print(f"\033[0;32m$ {args.get('command')}\033[0m")
            elif fn_name == "patch_file":
                print(f"\033[0;33mPatch -> {args.get('rel_path')}\033[0m")
            elif fn_name == "write_file":
                print(f"\033[0;33mWrite -> {args.get('rel_path')}\033[0m")

            if fn_name == "finish":
                summary = args.get("summary", "Tugas selesai.")
                print(f"\n\033[1;32m✅ [TASK COMPLETE]:\033[0m\n{summary}")
                print(f"\nTotal Calls: {llm.call_count} | Tokens: {llm.total_tokens}")
                save_session(messages)
                return messages

            result = await tools.execute_tool(fn_name, args)
            preview = result[:200] + "..." if len(result) > 200 else result
            print(f"\033[0;35m↳ Output:\033[0m {preview}\n")

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result
            })

    print("\n\033[1;33m⚠️ Batas maksimum putaran tercapai sebelum 'finish' dipanggil.\033[0m")
    save_session(messages)
    return messages

async def interactive_repl(initial_task: Optional[str] = None):
    cwd_name = config.WORKSPACE.name
    session_file = get_session_file()
    
    # Inisialisasi atau muat sesi sebelumnya
    saved_messages = load_session()
    if saved_messages:
        messages = saved_messages
        print(f"\033[1;32m📦 Sesi sebelumnya dimuat ({len(messages)} pesan di memori).\033[0m")
        print("\033[0;33m(Ketik '/clear' untuk mereset riwayat sesi kapan saja)\033[0m")
    else:
        messages = [
            {
                "role": "system",
                "content": f"Workspace: {config.WORKSPACE.as_posix()}\nOS: {sys.platform}"
            }
        ]

    print("\033[1;36m=== PRIMAGINARY ORKESTRA AI (BUILD MODE) ===\033[0m")
    print(f"Workspace : \033[1m{config.WORKSPACE}\033[0m")
    print(f"Model     : {config.MODEL_NAME}")
    print("\033[0;37mPerintah khusus: '/exit' untuk keluar, '/clear' untuk reset, '/models' untuk ganti model.\033[0m\n")

    current_task = initial_task

    while True:
        if not current_task:
            try:
                current_task = input(f"\n\033[1;32m[primaginary-ai @ {cwd_name}]\033[0m > ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nSampai jumpa!")
                break

        if not current_task:
            continue

        cmd = current_task.lower()
        if cmd in ("/exit", "exit", "quit"):
            print("Keluar dari Primaginary AI.")
            break
        elif cmd in ("/clear", "clear"):
            if session_file.is_file():
                session_file.unlink()
            messages = [
                {
                    "role": "system",
                    "content": f"Workspace: {config.WORKSPACE.as_posix()}\nOS: {sys.platform}"
                }
            ]
            print("\033[1;32m🧹 Riwayat sesi dibersihkan. Memulai dari nol.\033[0m")
            current_task = None
            continue
        elif cmd in ("/models", "models"):
            await select_model_interactive()
            current_task = None
            continue

        # Jalankan eksekusi tugas
        messages = await execute_task_loop(current_task, messages)
        save_session(messages)
        current_task = None

def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--models", "-m"):
        asyncio.run(select_model_interactive())
        return

    initial_task = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else None
    asyncio.run(interactive_repl(initial_task))

if __name__ == "__main__":
    main()
