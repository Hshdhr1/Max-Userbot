"""Статический сканер опасных паттернов в `.py`-модулях.

Идея: модули из каталога / .dlm могут содержать произвольный код. Перед тем как
их выполнять — пытаемся просканировать AST + сырой текст и вывести в Web UI
список реальных угроз: то что заполнит диск (`fallocate`/`dd`), отформатирует
его (`mkfs`/`rm -rf /`), запустит шелл (`subprocess(... shell=True)`),
вытащит секреты (`/etc/shadow`, `~/.ssh`) и т.п.

Сканер намеренно консервативный: false-negative > false-positive. Это первый
эшелон защиты, не замена sandboxing'у.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# ----------------------------- сигнатуры ------------------------------------

# Шелл-команды, которые мы считаем «реально опасными» в строковых литералах.
_SHELL_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    # (regex, severity, label, suggestion)
    (
        r"\brm\s+-rf?\s+(?:/|/\*|\$HOME|~|\$\{HOME\}|/etc|/usr|/var|/home)\b",
        "critical",
        "rm -rf по системному пути",
        "Удалит важные данные. Удалите модуль или замените на безопасный пейлоад.",
    ),
    (
        r"\bfallocate\s+-l\s*\d+",
        "critical",
        "fallocate большого файла",
        "Способ заполнить диск — отказывается обслуживать. Удалите модуль.",
    ),
    (
        r"\btruncate\s+-s\s*\d+[KMGT]",
        "critical",
        "truncate -s NG (заполнение диска)",
        "Удалите модуль или замените на нормальные операции с файлами.",
    ),
    (
        r"\bdd\s+if=/dev/(?:zero|urandom|random)\b",
        "critical",
        "dd if=/dev/{zero,urandom} (фабрика мусора на диск)",
        "Удалите модуль.",
    ),
    (
        r"\bmkfs\.[a-z0-9]+\b",
        "critical",
        "mkfs.* (форматирование ФС)",
        "Никакому юзерботу не нужно форматировать ФС. Удалите модуль.",
    ),
    (
        r":\(\)\s*\{\s*:\|:&\s*\};?\s*:",
        "critical",
        "fork-bomb «:(){ :|:& };:»",
        "Удалит систему через несколько секунд. Удалите модуль.",
    ),
    (
        r"\bchmod\s+-R?\s+777\s+/",
        "high",
        "chmod 777 на /",
        "Открывает все файлы для записи всем. Удалите модуль.",
    ),
    (
        r"\bcurl\s+[^|]+\|\s*(?:bash|sh|python)",
        "high",
        "curl ... | sh — slug-pipe",
        "Удаленный скрипт исполняется без проверки. Удалите модуль.",
    ),
    (
        r"\bwget\s+[^|]+\|\s*(?:bash|sh|python)",
        "high",
        "wget ... | sh",
        "Удалите модуль.",
    ),
    (
        r"\b/etc/(?:shadow|sudoers|passwd)\b",
        "high",
        "Чтение /etc/shadow или sudoers",
        "Кража учеток. Удалите модуль.",
    ),
    (
        r"(?:~|\$HOME|/home/[^/\s'\"]+|/root)/\.ssh/(?:id_rsa|id_ed25519|id_ecdsa|authorized_keys|known_hosts)?",
        "high",
        "Доступ к ~/.ssh ключам",
        "Кража SSH-ключей. Удалите модуль.",
    ),
    (
        r"history\s+-c\b|\bunset\s+HISTFILE\b",
        "medium",
        "Очистка истории shell (anti-forensics)",
        "Подозрительно — модуль пытается замести следы. Проверьте автора.",
    ),
)

# AST-сигнатуры — на узлы Call, Attribute и т.п.
# (severity, label, suggestion)
@dataclass
class AstHit:
    severity: str
    label: str
    suggestion: str


@dataclass
class Threat:
    module: str
    file: str
    line: int
    severity: str  # critical | high | medium
    label: str
    snippet: str
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "file": self.file,
            "line": self.line,
            "severity": self.severity,
            "label": self.label,
            "snippet": self.snippet,
            "suggestion": self.suggestion,
        }


@dataclass
class ModuleScan:
    module: str
    file: str
    threats: list[Threat] = field(default_factory=list)

    @property
    def severity(self) -> str:
        if any(t.severity == "critical" for t in self.threats):
            return "critical"
        if any(t.severity == "high" for t in self.threats):
            return "high"
        if self.threats:
            return "medium"
        return "ok"

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "file": self.file,
            "severity": self.severity,
            "threats": [t.to_dict() for t in self.threats],
        }


# ------------------------------- AST visitor --------------------------------


_DANGEROUS_FUNC_CALLS: dict[tuple[str, ...], AstHit] = {
    ("os", "system"): AstHit(
        "high",
        "os.system(...) — запуск шелла без аргументов",
        "Используйте subprocess.run(args=[...]) с явным списком аргументов и shell=False.",
    ),
    ("os", "popen"): AstHit(
        "high",
        "os.popen(...)",
        "Аналогично os.system — потенциальная command injection.",
    ),
    ("os", "execv"): AstHit("high", "os.execv(...)", "Замена процесса. Проверьте, что аргументы константны."),
    ("os", "execvp"): AstHit("high", "os.execvp(...)", "Замена процесса."),
    ("subprocess", "getoutput"): AstHit(
        "high",
        "subprocess.getoutput(...) — внутри shell=True",
        "Используйте subprocess.run([...], capture_output=True) без shell.",
    ),
    ("subprocess", "getstatusoutput"): AstHit(
        "high",
        "subprocess.getstatusoutput(...) — внутри shell=True",
        "Используйте subprocess.run без shell.",
    ),
    ("shutil", "rmtree"): AstHit(
        "medium",
        "shutil.rmtree(...) — рекурсивное удаление",
        "Убедитесь, что путь жёстко зафиксирован и не приходит из пользовательского ввода.",
    ),
    ("pickle", "loads"): AstHit(
        "high",
        "pickle.loads(...) — RCE при недоверенных данных",
        "Используйте json или подписанный msgpack.",
    ),
    ("pickle", "load"): AstHit(
        "high",
        "pickle.load(...) — RCE при недоверенных данных",
        "Используйте json или подписанный msgpack.",
    ),
    ("marshal", "loads"): AstHit("high", "marshal.loads(...)", "RCE-потенциал — используйте json."),
}


_BUILTIN_DANGEROUS = {"eval", "exec", "compile", "__import__"}


def _attr_chain(node: ast.AST) -> tuple[str, ...]:
    """Возвращает имя цепочкой типа `subprocess.Popen` → ('subprocess', 'Popen')."""
    parts: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return tuple(reversed(parts))


def _scan_subprocess_call(call: ast.Call) -> AstHit | None:
    """`subprocess.run(... shell=True)` или Popen с shell=True — high."""
    chain = _attr_chain(call.func)
    if not chain or chain[0] != "subprocess":
        return None
    if len(chain) < 2:
        return None
    target = chain[1]
    if target not in {"run", "Popen", "call", "check_call", "check_output"}:
        return None
    for kw in call.keywords:
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return AstHit(
                "high",
                f"subprocess.{target}(... shell=True)",
                "Это classic command-injection. Передавайте список аргументов и shell=False.",
            )
    return None


def _scan_call(call: ast.Call) -> AstHit | None:
    chain = _attr_chain(call.func)
    if chain and len(chain) >= 2 and (chain[0], chain[-1]) in _DANGEROUS_FUNC_CALLS:
        return _DANGEROUS_FUNC_CALLS[(chain[0], chain[-1])]

    sp = _scan_subprocess_call(call)
    if sp:
        return sp

    if isinstance(call.func, ast.Name) and call.func.id in _BUILTIN_DANGEROUS:
        # eval/exec/compile — критично только если аргумент не литерал.
        if call.args and isinstance(call.args[0], ast.Constant):
            return None
        return AstHit(
            "critical" if call.func.id in {"eval", "exec"} else "high",
            f"Builtin {call.func.id}(...) с не-константным аргументом",
            "RCE. Если действительно нужно — обернуть в sandbox или удалить модуль.",
        )
    return None


def _scan_string_literal(s: str) -> list[tuple[str, str, str]]:
    """Возвращает список (severity, label, suggestion) для всех совпавших шелл-паттернов."""
    out: list[tuple[str, str, str]] = []
    for rx, sev, label, hint in _SHELL_PATTERNS:
        if re.search(rx, s, re.IGNORECASE):
            out.append((sev, label, hint))
    return out


# --------------------------------- API --------------------------------------


def scan_source(module_name: str, file: str, source: str) -> ModuleScan:
    """Сканирует исходник одного модуля."""
    scan = ModuleScan(module=module_name, file=file)
    lines = source.splitlines()

    def _add(line: int, severity: str, label: str, suggestion: str) -> None:
        snippet = ""
        if 0 < line <= len(lines):
            snippet = lines[line - 1].strip()
            if len(snippet) > 200:
                snippet = snippet[:200] + "…"
        scan.threats.append(
            Threat(
                module=module_name,
                file=file,
                line=line,
                severity=severity,
                label=label,
                snippet=snippet,
                suggestion=suggestion,
            )
        )

    # 1) AST — устойчивый разбор вызовов.
    try:
        tree = ast.parse(source, filename=file)
    except SyntaxError as exc:
        _add(exc.lineno or 0, "high", "SyntaxError при разборе модуля", str(exc))
        tree = None

    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                hit = _scan_call(node)
                if hit:
                    _add(getattr(node, "lineno", 0), hit.severity, hit.label, hit.suggestion)
            # 2) Строковые литералы — ищем шелл-паттерны.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for sev, label, hint in _scan_string_literal(node.value):
                    _add(getattr(node, "lineno", 0), sev, label, hint)

    # 3) Дополнительный fallback: простые regex по всему файлу — на случай
    # если код в строках, которые не дошли до AST (мульти-строки в exec и т.п.).
    for ln, raw in enumerate(lines, start=1):
        # пропускаем то, что точно мусор: коммент, пусто.
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for sev, label, hint in _scan_string_literal(raw):
            # уже добавили — не дублируем
            if any(t.line == ln and t.label == label for t in scan.threats):
                continue
            _add(ln, sev, label, hint)

    return scan


def scan_directory(modules_dir: Path | str) -> list[ModuleScan]:
    p = Path(modules_dir)
    out: list[ModuleScan] = []
    if not p.exists():
        return out
    for f in sorted(p.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append(scan_source(f.stem, str(f), source))
    return out


def summary(scans: Iterable[ModuleScan]) -> dict:
    """Сводка для UI: сколько модулей, сколько по уровням, сколько чистых."""
    out = {"total": 0, "ok": 0, "medium": 0, "high": 0, "critical": 0}
    for s in scans:
        out["total"] += 1
        out[s.severity] += 1
    return out
