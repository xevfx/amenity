from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from discord import app_commands
from discord.ext import commands

SCHEMA_VERSION = 2
DEFAULT_PREFIX = ""
DEFAULT_OUTPUT_PATH = "docs/commands.json"


def _clean_text(value: object, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _command_id(name: str) -> str:
    return name.replace(" ", ".")


def _annotation_to_str(annotation: object) -> str | None:
    if annotation is None or annotation is inspect._empty:
        return None
    name = getattr(annotation, "__name__", None)
    if name:
        return name
    module = getattr(annotation, "__module__", None)
    qualname = getattr(annotation, "__qualname__", None)
    if module and qualname and module != "builtins":
        return f"{module}.{qualname}"
    return str(annotation).replace("typing.", "")


def _bucket_type_name(value: object) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if name:
        return str(name)
    text = str(value)
    return text.rsplit(".", 1)[-1] if text else None


def _serialize_cooldown(command: commands.Command[Any, ..., Any]) -> dict[str, Any] | None:
    bucket = getattr(command, "_buckets", None)
    cooldown = getattr(bucket, "_cooldown", None)
    if cooldown is None:
        return None
    return {
        "rate": cooldown.rate,
        "per": cooldown.per,
        "bucket": _bucket_type_name(getattr(cooldown, "type", None)),
        "label": f"{cooldown.rate}/{int(cooldown.per) if cooldown.per == int(cooldown.per) else cooldown.per}s",
    }


def _serialize_concurrency(command: commands.Command[Any, ..., Any]) -> dict[str, Any] | None:
    concurrency = getattr(command, "_max_concurrency", None)
    if concurrency is None:
        return None
    return {
        "number": getattr(concurrency, "number", None),
        "bucket": _bucket_type_name(getattr(concurrency, "per", None)),
        "wait": bool(getattr(concurrency, "wait", False)),
    }


def _param_display(param: inspect.Parameter) -> str:
    name = param.name
    if param.kind is inspect.Parameter.VAR_POSITIONAL:
        return f"[{name}...]"
    if param.kind is inspect.Parameter.KEYWORD_ONLY:
        name = f"{name}..."
    return f"[{name}]" if param.default is not inspect._empty else f"<{name}>"


def _usage(command: commands.Command[Any, ..., Any], prefix: str) -> str:
    base = f"{prefix}{command.qualified_name}".strip()
    if command.usage:
        return f"{base} {command.usage}".strip()
    params = [_param_display(param) for param in command.clean_params.values()]
    return " ".join([base, *params]).strip()


def _serialize_prefix_params(command: commands.Command[Any, ..., Any]) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    for name, param in command.clean_params.items():
        default = None if param.default is inspect._empty else param.default
        if default is not None and not isinstance(default, str | int | float | bool | list | dict | tuple):
            default = str(default)
        params.append(
            {
                "name": name,
                "display": _param_display(param),
                "description": None,
                "required": param.default is inspect._empty,
                "default": default,
                "kind": param.kind.name.lower(),
                "type": _annotation_to_str(param.annotation),
            }
        )
    return params


def _serialize_app_params(command: app_commands.Command[Any, ..., Any]) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    for param in getattr(command, "parameters", []) or []:
        param_type = getattr(param, "type", None)
        choices = [
            {
                "name": choice.name,
                "value": choice.value,
            }
            for choice in (param.choices or [])
        ]
        params.append(
            {
                "name": param.name,
                "display": f"<{param.name}>" if getattr(param, "required", False) else f"[{param.name}]",
                "description": _clean_text(param.description, fallback=None),
                "required": bool(getattr(param, "required", False)),
                "default": None,
                "kind": "slash_option",
                "type": getattr(param_type, "name", None) or str(param_type),
                "choices": choices,
            }
        )
    return params


def _command_description(command: commands.Command[Any, ..., Any]) -> str:
    description = command.help or command.brief or command.description or command.short_doc
    if not description:
        description = inspect.getdoc(command.callback)
    return _clean_text(description, "No description provided.")


def _app_command_description(command: app_commands.Command[Any, ..., Any]) -> str:
    return _clean_text(getattr(command, "description", None), "No description provided.")


def _category_key(cog: commands.Cog | None) -> str:
    if cog is None:
        return "Uncategorized"
    group_name = getattr(cog, "group_name", None)
    if group_name:
        return _clean_text(group_name, "Uncategorized")
    return _clean_text(getattr(cog, "qualified_name", None), "Uncategorized")


def _cog_name(cog: commands.Cog | None) -> str:
    if cog is None:
        return "Uncategorized"
    return _clean_text(getattr(cog, "display_name", None) or getattr(cog, "qualified_name", None), "Uncategorized")


def _has_hidden_parent(command: commands.Command[Any, ..., Any]) -> bool:
    parent = command.parent
    while parent is not None:
        if parent.hidden:
            return True
        parent = parent.parent
    return False


def _is_public_prefix_command(command: commands.Command[Any, ..., Any], include_hidden: bool) -> bool:
    if include_hidden:
        return True
    if command.hidden or _has_hidden_parent(command) or command.qualified_name == "help":
        return False
    cog = command.cog
    return not bool(getattr(cog, "hidden", False) or getattr(cog, "owner_only", False))


def _is_public_app_command(command: app_commands.Command[Any, ..., Any], include_hidden: bool) -> bool:
    if include_hidden:
        return True
    if command.name == "help":
        return False
    binding = getattr(command, "binding", None)
    return not bool(getattr(binding, "hidden", False) or getattr(binding, "owner_only", False))


def _walk_app_commands(tree: app_commands.CommandTree[Any]) -> list[app_commands.Command[Any, ..., Any]]:
    return sorted(tree.walk_commands(), key=lambda item: getattr(item, "qualified_name", item.name))


def _interaction_contexts(command: app_commands.Command[Any, ..., Any]) -> list[str]:
    allowed_contexts = getattr(command, "allowed_contexts", None)
    if allowed_contexts is None:
        return []
    contexts: list[str] = []
    for attr, label in (
        ("guilds", "servers"),
        ("dms", "dms"),
        ("private_channels", "private_channels"),
    ):
        if getattr(allowed_contexts, attr, False):
            contexts.append(label)
    return contexts


def _install_contexts(command: app_commands.Command[Any, ..., Any]) -> list[str]:
    allowed_installs = getattr(command, "allowed_installs", None)
    if allowed_installs is None:
        return []
    contexts: list[str] = []
    for attr, label in (("guilds", "servers"), ("users", "users")):
        if getattr(allowed_installs, attr, False):
            contexts.append(label)
    return contexts


def _base_doc_command(
    *,
    name: str,
    qualified_name: str,
    description: str,
    category: str,
    cog: str,
    parent: str | None,
    is_group: bool,
) -> dict[str, Any]:
    return {
        "id": _command_id(qualified_name),
        "name": name,
        "qualified_name": qualified_name,
        "display_name": qualified_name.replace(" ", " / "),
        "description": description,
        "category": category,
        "cog": cog,
        "parent": parent,
        "is_group": is_group,
        "subcommands": [],
        "availability": {
            "prefix": False,
            "slash": False,
        },
        "prefix": None,
        "slash": None,
        "search_terms": [],
    }


def _add_prefix_data(
    doc_command: dict[str, Any],
    command: commands.Command[Any, ..., Any],
    *,
    prefix: str,
) -> None:
    doc_command["availability"]["prefix"] = True
    doc_command["description"] = _command_description(command)
    doc_command["aliases"] = list(command.aliases)
    doc_command["prefix"] = {
        "name": command.name,
        "qualified_name": command.qualified_name,
        "aliases": list(command.aliases),
        "usage": _usage(command, prefix),
        "signature": command.signature,
        "parameters": _serialize_prefix_params(command),
        "cooldown": _serialize_cooldown(command),
        "max_concurrency": _serialize_concurrency(command),
    }


def _add_slash_data(doc_command: dict[str, Any], command: app_commands.Command[Any, ..., Any]) -> None:
    doc_command["availability"]["slash"] = True
    doc_command["slash"] = {
        "name": command.name,
        "qualified_name": getattr(command, "qualified_name", command.name),
        "mention": f"/{getattr(command, 'qualified_name', command.name)}",
        "parameters": _serialize_app_params(command),
        "contexts": _interaction_contexts(command),
        "installs": _install_contexts(command),
    }
    if doc_command["description"] == "No description provided.":
        doc_command["description"] = _app_command_description(command)


def _finalize_command(doc_command: dict[str, Any]) -> None:
    names = {
        doc_command["name"],
        doc_command["qualified_name"],
        doc_command["display_name"],
        doc_command["description"],
        doc_command["category"],
        doc_command["cog"],
    }
    prefix_data = doc_command.get("prefix") or {}
    names.update(prefix_data.get("aliases") or [])
    slash_data = doc_command.get("slash") or {}
    names.add(slash_data.get("mention"))
    doc_command["search_terms"] = sorted(_clean_text(value) for value in names if _clean_text(value))


def _group_children(commands_by_name: dict[str, dict[str, Any]]) -> None:
    for command in commands_by_name.values():
        parent = command["parent"]
        if not parent:
            continue
        parent_command = commands_by_name.get(parent)
        if parent_command is not None:
            parent_command["subcommands"].append(command["qualified_name"])
    for command in commands_by_name.values():
        command["subcommands"].sort()


def _build_categories(commands_by_name: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    categories: dict[str, dict[str, Any]] = {}
    for command in commands_by_name.values():
        category = command["category"]
        categories.setdefault(
            category,
            {
                "name": category,
                "commands": [],
                "top_level_commands": [],
            },
        )
        categories[category]["commands"].append(command)
        if command["parent"] is None:
            categories[category]["top_level_commands"].append(command)

    for category in categories.values():
        category["commands"].sort(key=lambda item: item["qualified_name"])
        category["top_level_commands"].sort(key=lambda item: item["qualified_name"])
        category["count"] = len(category["commands"])

    return sorted(categories.values(), key=lambda item: item["name"].lower())


def build_commands_payload(
    bot: commands.Bot,
    *,
    prefix: str | None = None,
    include_hidden: bool = False,
) -> dict[str, Any]:
    command_prefix = DEFAULT_PREFIX if prefix is None else prefix
    commands_by_name: dict[str, dict[str, Any]] = {}

    for command in sorted(bot.walk_commands(), key=lambda item: item.qualified_name):
        if not _is_public_prefix_command(command, include_hidden):
            continue
        cog = command.cog
        doc_command = commands_by_name.setdefault(
            command.qualified_name,
            _base_doc_command(
                name=command.name,
                qualified_name=command.qualified_name,
                description=_command_description(command),
                category=_category_key(cog),
                cog=_cog_name(cog),
                parent=command.parent.qualified_name if command.parent else None,
                is_group=isinstance(command, commands.Group),
            ),
        )
        _add_prefix_data(doc_command, command, prefix=command_prefix)

    for command in _walk_app_commands(bot.tree):
        if not _is_public_app_command(command, include_hidden):
            continue
        qualified_name = getattr(command, "qualified_name", command.name)
        binding = getattr(command, "binding", None)
        doc_command = commands_by_name.setdefault(
            qualified_name,
            _base_doc_command(
                name=command.name,
                qualified_name=qualified_name,
                description=_app_command_description(command),
                category=_category_key(binding),
                cog=_cog_name(binding),
                parent=command.parent.qualified_name if command.parent else None,
                is_group=isinstance(command, app_commands.Group),
            ),
        )
        _add_slash_data(doc_command, command)

    _group_children(commands_by_name)
    for command in commands_by_name.values():
        _finalize_command(command)

    commands_list = sorted(commands_by_name.values(), key=lambda item: item["qualified_name"])
    categories = _build_categories(commands_by_name)
    prefix_count = sum(1 for command in commands_list if command["availability"]["prefix"])
    slash_count = sum(1 for command in commands_list if command["availability"]["slash"])

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "prefix": command_prefix,
        "counts": {
            "commands": len(commands_list),
            "categories": len(categories),
            "prefix": prefix_count,
            "slash": slash_count,
            "hybrid": sum(
                1
                for command in commands_list
                if command["availability"]["prefix"] and command["availability"]["slash"]
            ),
        },
        "categories": categories,
        "commands": commands_list,
        "commands_by_id": {command["id"]: command for command in commands_list},
        "commands_by_name": {command["qualified_name"]: command for command in commands_list},
    }


def export_commands(
    bot: commands.Bot,
    output_path: str = DEFAULT_OUTPUT_PATH,
    *,
    prefix: str | None = None,
    include_hidden: bool = False,
) -> dict[str, Any]:
    payload = build_commands_payload(bot, prefix=prefix, include_hidden=include_hidden)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return payload
