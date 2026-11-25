#!/usr/bin/env python3
"""Unlock Inkling-2-Mini's native thinking levels in Strata's `control/serve/` runtime.

Inkling-2-Mini's `chat_template.jinja` natively supports `reasoning_effort`
(`none`=0, `minimal`=0.1, `low`=0.2, `medium`=0.7, `high`=0.9, `xhigh`/`max`=0.99
plus `<|content_thinking|>...<|end_message|>`), but upstream Strata 0.2.20 locks
`inkling_dialect` / `server.py` / `frontend.py` to thinking-off only.

Design (v2):
- API / Agent requests that omit `reasoning_effort` / `thinking` / `enable_thinking`
  default to `"none"` (`Thinking effort level: 0`), preventing short `max_tokens`
  clients (Ollama, Anthropic, `Check-Inkling.py`, tool helpers) from burning their
  entire output budget inside hidden reasoning and returning empty answers.
- Explicit requests (`reasoning_effort: low|medium|high|...`, Strata Web Chat
  Settings `Off / Low / Medium / High`, `POST /settings` shared defaults,
  `chat_template_kwargs.enable_thinking=true`, Anthropic `thinking.type="enabled"` /
  `budget_tokens` / `output_config.effort`, and GUI quick-test selector) activate
  Inkling-2-Mini's native thinking channel cleanly.

Because `control/` is downloaded at install time and gitignored, this script applies
idempotent patches directly to `control/serve/`, supporting both fresh upstream
Strata 0.2.20 and upgrading existing v1-patched installations seamlessly.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTROL_DIR = ROOT / "control"


def _patch_file(
    path: Path,
    marker: str,
    replacements: list[tuple[str | tuple[str, ...], str]],
) -> str:
    if not path.is_file():
        return "missing"
    src = path.read_text(encoding="utf-8")
    if marker in src:
        return "already_patched"
    newline = "\r\n" if "\r\n" in src else "\n"
    work = src.replace("\r\n", "\n")
    for idx, (olds, new) in enumerate(replacements, start=1):
        if new in work:
            continue
        candidates = (olds,) if isinstance(olds, str) else olds
        matched = next((c for c in candidates if c in work), None)
        if matched is None:
            preview = candidates[0][:80] if candidates else ""
            raise RuntimeError(
                f"Inkling thinking patch anchor #{idx} not found in {path}; "
                f"expected snippet starting with {preview!r}"
            )
        work = work.replace(matched, new, 1)
    if newline == "\r\n":
        work = work.replace("\n", "\r\n")
    path.write_text(work, encoding="utf-8")
    return "patched"


def _patch_inkling_dialect(serve_dir: Path) -> str:
    marker = "patched by Strata-inkling v2"
    path = serve_dir / "inkling_dialect.py"

    upstream_thinking_block = (
        '# ---- thinking mode: Inkling-2-Mini has NO thinking mode (default is off / "none", and that\'s it) ----\n'
        'DEFAULT_EFFORT = "none"\n'
        "EFFORT = {\n"
        '    "none": "none", "off": "none", "no_think": "none", "disabled": "none", "false": "none", "0": "none", "0.0": "none",\n'
        "}\n"
        "\n"
        "\n"
        "def normalize_effort(value=None) -> str:\n"
        '    """Inkling-2-Mini has no thinking mode: always normalizes to `"none"` (`Thinking effort level: 0`)."""\n'
        "    return DEFAULT_EFFORT\n"
        "\n"
        "\n"
        "def template_kwargs(kwargs: dict) -> dict:\n"
        '    """Normalize kwargs for Inkling-2-Mini\'s chat_template.jinja: always enforces thinking off (`reasoning_effort="none"`)."""\n'
        "    out = dict(kwargs)\n"
        '    out.pop("thinking_mode", None)\n'
        '    out["enable_thinking"] = False\n'
        '    out["reasoning_effort"] = DEFAULT_EFFORT\n'
        "    return out"
    )

    v1_thinking_block = (
        "# ---- thinking mode: Inkling-2-Mini native reasoning_effort (patched by Strata-inkling) ----\n"
        'DEFAULT_EFFORT = "high"\n'
        "EFFORT = {\n"
        '    "none": None, "off": None, "no_think": None, "disabled": None, "false": None, "0": None, "0.0": None,\n'
        '    "minimal": "minimal", "min": "minimal",\n'
        '    "low": "low",\n'
        '    "medium": "medium", "med": "medium",\n'
        '    "high": "high", "auto": "high", "default": "high",\n'
        '    "xhigh": "xhigh", "max": "max", "maximum": "max",\n'
        "}\n"
        "\n"
        "\n"
        "def normalize_effort(value=None) -> str | float:\n"
        '    """Normalize client reasoning_effort / thinking_mode for Inkling-2-Mini\'s chat_template.jinja."""\n'
        "    if value is False:\n"
        '        return "none"\n'
        '    if value is True or value is None or value == "":\n'
        "        return DEFAULT_EFFORT\n"
        "    if isinstance(value, (int, float)) and not isinstance(value, bool):\n"
        "        v = float(value)\n"
        "        if v <= 0.0:\n"
        '            return "none"\n'
        "        return 0.99 if v > 0.99 else v\n"
        "    key = str(value).strip().lower()\n"
        "    if key in EFFORT:\n"
        "        mapped = EFFORT[key]\n"
        '        return "none" if mapped is None else mapped\n'
        "    try:\n"
        "        v = float(key)\n"
        "        if v <= 0.0:\n"
        '            return "none"\n'
        "        return 0.99 if v > 0.99 else v\n"
        "    except ValueError:\n"
        "        return DEFAULT_EFFORT\n"
        "\n"
        "\n"
        "def template_kwargs(kwargs: dict) -> dict:\n"
        '    """Normalize kwargs for Inkling-2-Mini\'s chat_template.jinja, honoring off/low/medium/high/xhigh/max and 0..0.99."""\n'
        "    out = dict(kwargs)\n"
        '    if "thinking_mode" in out:\n'
        '        tm = out.pop("thinking_mode")\n'
        '        if out.get("reasoning_effort") in (None, ""):\n'
        '            out["reasoning_effort"] = tm\n'
        '    if out.get("enable_thinking") is False:\n'
        '        out["reasoning_effort"] = "none"\n'
        '    elif out.get("reasoning_effort") not in (None, ""):\n'
        '        out["reasoning_effort"] = normalize_effort(out["reasoning_effort"])\n'
        "    else:\n"
        '        out["reasoning_effort"] = DEFAULT_EFFORT\n'
        '    out["enable_thinking"] = out["reasoning_effort"] != "none"\n'
        "    return out"
    )

    v2_thinking_block = (
        "# ---- thinking mode: Inkling-2-Mini native reasoning_effort (patched by Strata-inkling v2) ----\n"
        'DEFAULT_EFFORT = "none"\n'
        "EFFORT = {\n"
        '    "none": None, "off": None, "no_think": None, "disabled": None, "false": None, "0": None, "0.0": None,\n'
        '    "minimal": "minimal", "min": "minimal",\n'
        '    "low": "low",\n'
        '    "medium": "medium", "med": "medium",\n'
        '    "high": "high", "auto": "high", "default": "high",\n'
        '    "xhigh": "xhigh", "max": "max", "maximum": "max",\n'
        "}\n"
        "\n"
        "\n"
        "def normalize_effort(value=None) -> str | float:\n"
        '    """Normalize client reasoning_effort / thinking_mode for Inkling-2-Mini\'s chat_template.jinja."""\n'
        "    if value is False:\n"
        '        return "none"\n'
        "    if value is True:\n"
        '        return "high"\n'
        '    if value is None or value == "":\n'
        "        return DEFAULT_EFFORT\n"
        "    if isinstance(value, (int, float)) and not isinstance(value, bool):\n"
        "        v = float(value)\n"
        "        if v <= 0.0:\n"
        '            return "none"\n'
        "        return 0.99 if v > 0.99 else v\n"
        "    key = str(value).strip().lower()\n"
        "    if key in EFFORT:\n"
        "        mapped = EFFORT[key]\n"
        '        return "none" if mapped is None else mapped\n'
        "    try:\n"
        "        v = float(key)\n"
        "        if v <= 0.0:\n"
        '            return "none"\n'
        "        return 0.99 if v > 0.99 else v\n"
        "    except ValueError:\n"
        "        return DEFAULT_EFFORT\n"
        "\n"
        "\n"
        "def template_kwargs(kwargs: dict) -> dict:\n"
        '    """Normalize kwargs for Inkling-2-Mini\'s chat_template.jinja: defaults to off ("none") when unspecified,\n'
        '    and honors off/low/medium/high/xhigh/max, 0..0.99, or explicit enable_thinking=True."""\n'
        "    out = dict(kwargs)\n"
        '    if "thinking_mode" in out:\n'
        '        tm = out.pop("thinking_mode")\n'
        '        if out.get("reasoning_effort") in (None, ""):\n'
        '            out["reasoning_effort"] = tm\n'
        '    if out.get("enable_thinking") is False:\n'
        '        out["reasoning_effort"] = "none"\n'
        '    elif out.get("reasoning_effort") not in (None, ""):\n'
        '        out["reasoning_effort"] = normalize_effort(out["reasoning_effort"])\n'
        '    elif out.get("enable_thinking") is True:\n'
        '        out["reasoning_effort"] = "high"\n'
        "    else:\n"
        '        out["reasoning_effort"] = DEFAULT_EFFORT\n'
        '    out["enable_thinking"] = out["reasoning_effort"] != "none"\n'
        "    return out"
    )

    replacements: list[tuple[str | tuple[str, ...], str]] = [
        (
            'ENDOFTEXT = "<|endoftext|>"',
            'ENDOFTEXT = "<|endoftext|>"\n'
            'REASONING_CLOSE = f"\\n{END_MSG}{MSG_MODEL}{CONTENT_TEXT}"\n'
            'REASONING_WRAP_UP = f"\\n\\nI have thought about this long enough; time to give my answer.{END_MSG}{MSG_MODEL}{CONTENT_TEXT}"\n'
            'REASONING_WRAP_UP_TOOL = f"\\n\\nI have thought about this long enough; time to give my answer.{END_MSG}"',
        ),
        (
            "    def __init__(self):\n"
            '        self.state = "idle"          # idle | header | think | text | tool_json | tool_text\n'
            '        self.buf = ""\n'
            '        self.header_acc = ""\n'
            '        self.tool_acc = ""\n'
            "        self.calls = 0\n"
            "        self._in_think = False",
            "    def __init__(self, *, allow_thinking: bool = True):\n"
            "        self.allow_thinking = bool(allow_thinking)\n"
            '        self.state = "idle"          # idle | header | think | text | tool_json | tool_text\n'
            '        self.buf = ""\n'
            '        self.header_acc = ""\n'
            '        self.tool_acc = ""\n'
            "        self.calls = 0\n"
            "        self._in_think = False",
        ),
        (
            '        if self.state == "think":\n'
            '            # Inkling-2-Mini has no thinking mode (off / "none" only): silently discard any raw <|content_thinking|> span.\n'
            "            return",
            '        if self.state == "think":\n'
            "            if self.allow_thinking:\n"
            '                out.append(("reasoning", chunk))\n'
            "            return",
        ),
        (
            (v1_thinking_block, upstream_thinking_block),
            v2_thinking_block,
        ),
        (
            "def finish_prompt(prompt: str, *, has_tools: bool = False, forced: bool = False) -> str:\n"
            '    """Append an explicit non-thinking turn opener to a rendered Inkling-2-Mini prompt (#6612, #6620, #6627).\n'
            "\n"
            "    Inkling-2-Mini's `chat_template.jinja` ends at `<|end_message|>` with no assistant opener, so even with\n"
            "    `Thinking effort level: 0` the model still generates `<|message_model|><|content_thinking|>...<|end_message|>`\n"
            "    (burning `max_tokens`, causing empty replies, and breaking multi-turn prefix reuse).\n"
            "    - Without tools: append `<|message_model|><|content_text|>` so generation starts directly in visible text\n"
            "      and matches the next turn's rendered assistant prefix verbatim.\n"
            "    - With tools: append an empty thinking turn `<|message_model|><|content_thinking|><|end_message|>` so the\n"
            "      model immediately selects either `<|message_model|><|content_text|>` or a tool invocation without wasting\n"
            "      tokens or looping inside hidden thinking.\n"
            "    Idempotent if the prompt already carries an opener or when a forced tool-call opener will be appended.\n"
            '    """\n'
            "    if not prompt or forced:\n"
            "        return prompt",
            "def finish_prompt(prompt: str, *, has_tools: bool = False, forced: bool = False, thinking: bool = False) -> str:\n"
            '    """Append an explicit non-thinking turn opener when thinking is disabled (#6612, #6620, #6627).\n'
            "\n"
            "    When thinking is enabled (`thinking=True`), leave the rendered prompt ending at `<|end_message|>` so\n"
            "    Inkling-2-Mini emits `<|message_model|><|content_thinking|>...<|end_message|><|message_model|><|content_text|>...`.\n"
            "    When thinking is disabled (`thinking=False`), append a non-thinking opener:\n"
            "    - Without tools: `<|message_model|><|content_text|>` so generation starts directly in visible text.\n"
            "    - With tools: `<|message_model|><|content_thinking|><|end_message|>` so tool selection starts immediately.\n"
            '    """\n'
            "    if not prompt or forced or thinking:\n"
            "        return prompt",
        ),
    ]
    return _patch_file(path, marker, replacements)


def _patch_frontend(serve_dir: Path) -> str:
    marker = "patched by Strata-inkling v2"
    path = serve_dir / "frontend.py"

    upstream_parser = (
        "class InklingOutputParser(JsonOutputParser):\n"
        '    """The parser for Inkling-2-Mini: translates `<|content_thinking|>`, `<|content_text|>`, `<|content_invoke_tool_json|>`,\n'
        "    `<|content_invoke_tool_text|>`, and `<|content_xml|>` channels via serve/inkling_dialect.py.\n"
        "    Starts in content mode (`thinking=False`) because Inkling-2-Mini opens and closes thinking explicitly with `<|content_thinking|>`,\n"
        '    so `reasoning_effort=none`, interleaved thinking, and direct tool calls all parse cleanly."""\n'
        "\n"
        "    def __init__(self, thinking: bool = True, tools: list[dict] | None = None, stream_tools: bool = False,\n"
        "                 recover: bool = False, declared_only: bool = False, deferred: bool = False):\n"
        "        super().__init__(thinking=False, tools=tools, stream_tools=stream_tools, recover=True,\n"
        "                         declared_only=declared_only, deferred=deferred)\n"
        "        from serve import inkling_dialect\n"
        "        self._dialect = inkling_dialect.InklingDialect()\n"
        "\n"
        "    def _dispatch(self, chunks: list[tuple[str, str]]) -> list[Event]:\n"
        "        out: list[Event] = []\n"
        "        for kind, chunk in chunks:\n"
        '            if kind == "reasoning":\n'
        '                out.append(Event("reasoning", chunk))\n'
        "            elif chunk:\n"
        "                out.extend(super().feed(chunk))\n"
        "        return out\n"
        "\n"
        "    def feed(self, delta: str) -> list[Event]:\n"
        "        return self._dispatch(self._dialect.feed_channels(delta))\n"
        "\n"
        "    def finish(self, reason: str | None = None) -> list[Event]:\n"
        '        out = self._dispatch(self._dialect.feed_channels("", final=True))\n'
        "        return out + super().finish(reason)"
    )

    v1_parser = (
        "class InklingOutputParser(JsonOutputParser):\n"
        '    """The parser for Inkling-2-Mini (patched by Strata-inkling): translates `<|content_thinking|>`, `<|content_text|>`,\n'
        "    `<|content_invoke_tool_json|>`, `<|content_invoke_tool_text|>`, and `<|content_xml|>` channels via serve/inkling_dialect.py,\n"
        '    tracking `.state` (`"reasoning"` vs content/call) and `.clean` for budget wrap-up and thinking recovery."""\n'
        "\n"
        "    def __init__(self, thinking: bool = True, tools: list[dict] | None = None, stream_tools: bool = False,\n"
        "                 recover: bool = False, declared_only: bool = False, deferred: bool = False):\n"
        "        super().__init__(thinking=False, tools=tools, stream_tools=stream_tools, recover=True,\n"
        "                         declared_only=declared_only, deferred=deferred)\n"
        "        from serve import inkling_dialect\n"
        '        self._content_state = "content"\n'
        "        self._dialect = inkling_dialect.InklingDialect(allow_thinking=thinking)\n"
        "\n"
        "    @property\n"
        "    def clean(self) -> bool:\n"
        "        return super().clean and not self._dialect.buf\n"
        "\n"
        "    def _sync_state(self) -> None:\n"
        '        if self._dialect.state == "think" and self._dialect.allow_thinking:\n'
        '            self.state = "reasoning"\n'
        "        else:\n"
        "            self.state = self._content_state\n"
        "\n"
        "    def _dispatch(self, chunks: list[tuple[str, str]]) -> list[Event]:\n"
        "        out: list[Event] = []\n"
        "        for kind, chunk in chunks:\n"
        '            if kind == "reasoning":\n'
        '                out.append(Event("reasoning", chunk))\n'
        "            elif chunk:\n"
        "                self.state = self._content_state\n"
        "                out.extend(super().feed(chunk))\n"
        "                self._content_state = self.state\n"
        "        self._sync_state()\n"
        "        return out\n"
        "\n"
        "    def feed(self, delta: str) -> list[Event]:\n"
        "        return self._dispatch(self._dialect.feed_channels(delta))\n"
        "\n"
        "    def finish(self, reason: str | None = None) -> list[Event]:\n"
        '        out = self._dispatch(self._dialect.feed_channels("", final=True))\n'
        "        self.state = self._content_state\n"
        "        fin = super().finish(reason)\n"
        "        self._content_state = self.state\n"
        "        self._sync_state()\n"
        "        return out + fin"
    )

    v2_parser = v1_parser.replace(
        "(patched by Strata-inkling)",
        "(patched by Strata-inkling v2)",
        1,
    )

    replacements: list[tuple[str | tuple[str, ...], str]] = [
        (
            '                    "supports_parallel_tool_calls": inkling_calls(2),\n'
            '                    "supports_preserve_reasoning": False}',
            '                    "supports_parallel_tool_calls": inkling_calls(2),\n'
            '                    "supports_preserve_reasoning": "strata_caps_reasoning" in render(history, tools)}',
        ),
        (
            "    if value is False:\n"
            '        return {"reasoning_effort": "none"} if _FAMILY == "inkling2" else {"enable_thinking": False}\n'
            '    if _FAMILY == "hy4":\n'
            "        from serve import hy4_dialect\n"
            "        mode = hy4_dialect.normalize_effort(value)\n"
            '        return {"enable_thinking": False} if mode == "no_think" else {"reasoning_effort": mode}\n'
            '    if _FAMILY == "inkling2":\n'
            "        from serve import inkling_dialect\n"
            '        return {"reasoning_effort": inkling_dialect.normalize_effort(value)}',
            "    if value is False:\n"
            '        return {"enable_thinking": False, "reasoning_effort": "none"} if _FAMILY == "inkling2" else {"enable_thinking": False}\n'
            '    if _FAMILY == "hy4":\n'
            "        from serve import hy4_dialect\n"
            "        mode = hy4_dialect.normalize_effort(value)\n"
            '        return {"enable_thinking": False} if mode == "no_think" else {"reasoning_effort": mode}\n'
            '    if _FAMILY == "inkling2":\n'
            "        from serve import inkling_dialect\n"
            "        mode = inkling_dialect.normalize_effort(value)\n"
            '        return {"enable_thinking": False, "reasoning_effort": "none"} if mode == "none" else {"reasoning_effort": mode}',
        ),
        (
            "    for k, v in ctk.items():\n"
            '        if k == "enable_thinking" and not v:\n'
            '            kwargs = {"enable_thinking": False}\n'
            '        elif k in ("reasoning_effort", "thinking_mode") and "enable_thinking" not in kwargs:\n'
            "            kwargs.update(effort_kwargs(v))",
            "    for k, v in ctk.items():\n"
            '        if k == "enable_thinking" and not v:\n'
            '            kwargs = {"enable_thinking": False}\n'
            '        elif k == "enable_thinking" and v and "reasoning_effort" not in kwargs and "enable_thinking" not in kwargs:\n'
            '            kwargs["enable_thinking"] = True\n'
            '        elif k in ("reasoning_effort", "thinking_mode") and kwargs.get("enable_thinking") is not False:\n'
            "            kwargs.update(effort_kwargs(v))",
        ),
        (
            '    elif isinstance(thinking, dict) and thinking.get("budget_tokens"):\n'
            '        kwargs.update(budget_effort(thinking["budget_tokens"]))\n'
            '    elif thinking is None and not req.get("reasoning_budget_tokens") and not think_unasked:',
            '    elif isinstance(thinking, dict) and thinking.get("budget_tokens"):\n'
            '        kwargs.update(budget_effort(thinking["budget_tokens"]))\n'
            '    elif isinstance(thinking, dict) and thinking.get("type") in ("enabled", "adaptive"):\n'
            '        kwargs["enable_thinking"] = True\n'
            '    elif thinking is None and not req.get("reasoning_budget_tokens") and not think_unasked:',
        ),
        (
            (v1_parser, upstream_parser),
            v2_parser,
        ),
    ]
    return _patch_file(path, marker, replacements)


def _patch_server(serve_dir: Path) -> str:
    marker = "Inkling-2-Mini thinking levels unlocked v2"
    path = serve_dir / "server.py"

    upstream_effort_default = (
        "    def effort_default(self) -> str | None:\n"
        '        """The thinking level of a request that names none: the Chat settings shared with apps first, then the config\'s\n'
        '        "reasoning_effort" (#1641).  Every API reads it here (the Responses API did not: 0.1.44.1)."""\n'
        "        if self.inkling:\n"
        "            return None\n"
        '        return self.shared.get("reasoning_effort") or self.config_effort'
    )
    v1_effort_default = (
        "    def effort_default(self) -> str | None:\n"
        '        """The thinking level of a request that names none: the Chat settings shared with apps first, then the config\'s\n'
        '        "reasoning_effort" (#1641).  Inkling-2-Mini thinking levels unlocked."""\n'
        '        return self.shared.get("reasoning_effort") or self.config_effort'
    )
    v2_effort_default = (
        "    def effort_default(self) -> str | None:\n"
        '        """The thinking level of a request that names none: the Chat settings shared with apps first, then the config\'s\n'
        '        "reasoning_effort" (#1641).  Inkling-2-Mini thinking levels unlocked v2."""\n'
        '        return self.shared.get("reasoning_effort") or self.config_effort'
    )

    replacements: list[tuple[str | tuple[str, ...], str]] = [
        (
            (v1_effort_default, upstream_effort_default),
            v2_effort_default,
        ),
        (
            "        if self.inkling:                               # Inkling-2-Mini: skip hidden <|content_thinking|> at the prompt boundary (#6612, #6620, #6627)\n"
            "            rendered = self.template.render(inkling_dialect.prepare_messages(messages), tools=tools, **inkling_dialect.template_kwargs(kwargs))\n"
            "            return inkling_dialect.finish_prompt(rendered, has_tools=bool(tools))",
            "        if self.inkling:                               # Inkling-2-Mini: honor thinking level; skip <|content_thinking|> only when off\n"
            "            tkw = inkling_dialect.template_kwargs(kwargs)\n"
            "            rendered = self.template.render(inkling_dialect.prepare_messages(messages), tools=tools, **tkw)\n"
            '            return inkling_dialect.finish_prompt(rendered, has_tools=bool(tools), thinking=tkw.get("enable_thinking") is not False)',
        ),
        (
            '        if force and kwargs.get("enable_thinking", True) is False:\n'
            "            ids = ids + self.tok.encode(force, parse_special=True)",
            '        no_think = (inkling_dialect.template_kwargs(kwargs).get("enable_thinking") is False) if self.inkling else (kwargs.get("enable_thinking", True) is False)\n'
            "        if force and no_think:\n"
            "            ids = ids + self.tok.encode(force, parse_special=True)",
        ),
        (
            '        return ids, kwargs.get("enable_thinking", True) is not False, max_new',
            '        thinking = (inkling_dialect.template_kwargs(kwargs).get("enable_thinking") is True) if self.inkling else (kwargs.get("enable_thinking", True) is not False)\n'
            "        return ids, thinking, max_new",
        ),
        (
            '                            and getattr(self.engine, "split_engine", False) is not True\n'
            '                            and getattr(self.engine, "hy4_engine", False) is not True):   # (the split & Hy4 parsers refuse hold=)',
            '                            and getattr(self.engine, "split_engine", False) is not True\n'
            '                            and getattr(self.engine, "hy4_engine", False) is not True\n'
            '                            and getattr(self.engine, "inkling_engine", False) is not True):   # (split, Hy4 & Inkling parsers refuse hold=)',
        ),
        (
            "                            close_retried = True\n"
            "                            extra = self.tok.encode(REASONING_CLOSE, parse_special=True)",
            "                            close_retried = True\n"
            "                            close_txt = inkling_dialect.REASONING_CLOSE if self.inkling else REASONING_CLOSE\n"
            "                            extra = self.tok.encode(close_txt, parse_special=True)",
        ),
        (
            "                        if wrap:\n"
            '                            budget = st["thinking_budget"] = None\n'
            '                            text = REASONING_WRAP_UP + (force or "")',
            "                        if wrap:\n"
            '                            budget = st["thinking_budget"] = None\n'
            "                            if self.inkling:\n"
            '                                text = (inkling_dialect.REASONING_WRAP_UP_TOOL if force else inkling_dialect.REASONING_WRAP_UP) + (force or "")\n'
            "                            else:\n"
            '                                text = REASONING_WRAP_UP + (force or "")',
        ),
        (
            '            svc.config_effort = clean_shared_defaults({"reasoning_effort": cfg["reasoning_effort"]}, hy4=hy4_kind.is_hy4(cfg)).get("reasoning_effort")',
            '            svc.config_effort = clean_shared_defaults({"reasoning_effort": cfg["reasoning_effort"]}, hy4=hy4_kind.is_hy4(cfg) or inkling_kind.is_inkling(cfg)).get("reasoning_effort")',
        ),
    ]
    return _patch_file(path, marker, replacements)


def ensure_patched(control_dir: Path | str | None = None) -> dict[str, str]:
    """Ensure `control/serve/` is patched to unlock Inkling-2-Mini native thinking levels."""
    base = Path(control_dir) if control_dir is not None else DEFAULT_CONTROL_DIR
    serve_dir = base / "serve"
    return {
        "inkling_dialect.py": _patch_inkling_dialect(serve_dir),
        "frontend.py": _patch_frontend(serve_dir),
        "server.py": _patch_server(serve_dir),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Unlock Inkling-2-Mini native thinking levels in Strata control/serve.")
    ap.add_argument("--control-dir", default=str(DEFAULT_CONTROL_DIR), help="Path to Strata control directory")
    args = ap.parse_args()
    try:
        res = ensure_patched(args.control_dir)
    except Exception as exc:
        print(f"[inkling-thinking-patch] ERROR: {exc}", file=sys.stderr)
        return 1
    for name, status in res.items():
        print(f"[inkling-thinking-patch] {name}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
