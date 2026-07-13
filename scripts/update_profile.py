"""Generate README.md and both profile SVG cards from profile.config.json."""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "profile.config.json"
PLACEHOLDER = re.compile(r"\{\{([a-z0-9_.]+)\}\}", re.IGNORECASE)


def github_get(username: str, path: str, params: dict[str, object] | None = None) -> object:
    url = f"https://api.github.com{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{username}-profile-readme",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def owned_repositories(username: str) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    page = 1
    while True:
        result = github_get(
            username,
            f"/users/{username}/repos",
            {"type": "owner", "sort": "full_name", "per_page": 100, "page": page},
        )
        if not isinstance(result, list):
            raise TypeError("GitHub repository response was not a list")
        repositories.extend(result)
        if len(result) < 100:
            return repositories
        page += 1


def add_months(value: dt.date, months: int) -> dt.date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def human_age(created_at: str, today: dt.date) -> str:
    created = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
    years = today.year - created.year
    anniversary = add_months(created, years * 12)
    if anniversary > today:
        years -= 1
        anniversary = add_months(created, years * 12)

    months = (today.year - anniversary.year) * 12 + today.month - anniversary.month
    month_mark = add_months(anniversary, months)
    if month_mark > today:
        months -= 1
        month_mark = add_months(anniversary, months)

    days = (today - month_mark).days

    def unit(number: int, label: str) -> str:
        return f"{number} {label}{'' if number == 1 else 's'}"

    return ", ".join((unit(years, "year"), unit(months, "month"), unit(days, "day")))


def github_values(username: str) -> dict[str, str]:
    profile = github_get(username, f"/users/{username}")
    if not isinstance(profile, dict):
        raise TypeError("GitHub profile response was not an object")

    repositories = owned_repositories(username)
    today = dt.datetime.now(dt.timezone.utc).date()
    return {
        "github.account_age": human_age(str(profile["created_at"]), today),
        "github.public_repos": str(profile["public_repos"]),
        "github.stars": str(sum(int(repo.get("stargazers_count", 0)) for repo in repositories)),
        "github.followers": str(profile["followers"]),
        "github.following": str(profile["following"]),
        "github.updated": today.isoformat(),
    }


def resolve(value: object, dynamic_values: dict[str, str]) -> str:
    text = str(value)

    def replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in dynamic_values:
            raise KeyError(f"Unknown or unavailable placeholder: {{{{{name}}}}}")
        return dynamic_values[name]

    return PLACEHOLDER.sub(replacement, text)


def expand_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    contacts = config.get("contacts", [])
    for row in config["rows"]:
        if row.get("type") == "contacts":
            expanded.extend(
                {"type": "field", "label": contact["label"], "value": contact["value"]}
                for contact in contacts
            )
        else:
            expanded.append(row)
    return expanded


def escaped(value: object) -> str:
    return html.escape(str(value), quote=False)


def prepare_ascii(lines: list[str], trim_empty_margins: bool) -> list[str]:
    """Remove only unused outer whitespace; never discard visible ASCII characters."""
    if not trim_empty_margins:
        return lines

    prepared = list(lines)
    while prepared and not prepared[0].strip():
        prepared.pop(0)
    while prepared and not prepared[-1].strip():
        prepared.pop()
    if not prepared:
        return [""]

    non_empty = [line for line in prepared if line.strip()]
    common_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
    return [line[common_indent:].rstrip() for line in prepared]


def dotted_field(label: str, value: str, width: int) -> str:
    prefix_length = len(label) + 3  # '. ' + label + ':'
    dots = "." * max(1, width - prefix_length - len(value) - 2)
    return (
        '<tspan class="muted">. </tspan>'
        f'<tspan class="key">{escaped(label)}</tspan>:'
        f'<tspan class="muted"> {dots} </tspan>'
        f'<tspan class="value">{escaped(value)}</tspan>'
    )


def dotted_pair(row: dict[str, Any], values: dict[str, str], width: int, left_width: int) -> str:
    left_label = resolve(row["left_label"], values)
    left_value = resolve(row["left_value"], values)
    right_label = resolve(row["right_label"], values)
    right_value = resolve(row["right_value"], values)

    left_prefix_length = len(left_label) + 3
    left_dots = "." * max(1, left_width - left_prefix_length - len(left_value) - 2)
    right_dots = "." * max(
        1,
        width - left_width - 3 - len(right_label) - 1 - len(right_value) - 2,
    )
    return (
        '<tspan class="muted">. </tspan>'
        f'<tspan class="key">{escaped(left_label)}</tspan>:'
        f'<tspan class="muted"> {left_dots} </tspan>'
        f'<tspan class="value">{escaped(left_value)}</tspan>'
        " | "
        f'<tspan class="key">{escaped(right_label)}</tspan>:'
        f'<tspan class="muted"> {right_dots} </tspan>'
        f'<tspan class="value">{escaped(right_value)}</tspan>'
    )


def render_row(
    row: dict[str, Any],
    values: dict[str, str],
    width: int,
    pair_left_width: int,
) -> str:
    row_type = row.get("type")
    if row_type == "blank":
        return '<tspan class="muted">. </tspan>'
    if row_type == "section":
        title = resolve(row["title"], values)
        prefix = f"- {title} "
        return escaped(prefix + "—" * max(3, width - len(prefix)))
    if row_type == "field":
        label = resolve(row["label"], values)
        value = resolve(row["value"], values)
        return dotted_field(label, value, width)
    if row_type == "pair":
        return dotted_pair(row, values, width, pair_left_width)
    raise ValueError(f"Unsupported row type: {row_type!r}")


def render_svg(
    config: dict[str, Any],
    theme: dict[str, Any],
    rows: list[dict[str, Any]],
    ascii_lines: list[str],
    values: dict[str, str],
) -> str:
    layout = config["layout"]
    width = int(layout["width"])
    minimum_height = int(layout["minimum_height"])
    font_size = int(layout["font_size"])
    left_x = int(layout["left_x"])
    right_x = int(layout["right_x"])
    first_y = int(layout["first_y"])
    line_height = int(layout["line_height"])
    bottom_padding = int(layout["bottom_padding"])
    content_width = int(layout["content_width_chars"])
    pair_left_width = int(layout["pair_left_width_chars"])

    content_bottom = first_y + line_height * len(rows) + bottom_padding
    height = max(minimum_height, content_bottom)

    ascii_options = config.get("ascii", {})
    ascii_scale = float(ascii_options.get("scale", 1.0))
    if ascii_scale <= 0:
        raise ValueError("ascii.scale must be greater than zero")
    character_width_ratio = float(ascii_options.get("character_width_ratio", 0.6))
    ascii_line_height_ratio = float(ascii_options.get("line_height_ratio", 1.2))
    ascii_right_padding = float(ascii_options.get("right_padding", 15))
    ascii_vertical_padding = float(ascii_options.get("vertical_padding", 20))
    ascii_max_font_size = float(ascii_options.get("max_font_size", font_size))

    max_ascii_columns = max((len(line) for line in ascii_lines), default=1)
    available_ascii_width = max(1.0, right_x - left_x - ascii_right_padding)
    available_ascii_height = max(1.0, height - 2 * ascii_vertical_padding)
    width_limited_size = available_ascii_width / (
        max_ascii_columns * character_width_ratio
    )
    height_limited_size = available_ascii_height / (
        max(1, len(ascii_lines)) * ascii_line_height_ratio
    )
    if ascii_options.get("auto_fit", True):
        ascii_font_size = min(
            ascii_max_font_size,
            width_limited_size,
            height_limited_size,
        ) * ascii_scale
    else:
        ascii_font_size = ascii_max_font_size * ascii_scale

    ascii_line_height = ascii_font_size * ascii_line_height_ratio
    ascii_block_width = max_ascii_columns * ascii_font_size * character_width_ratio
    ascii_block_height = (
        ascii_font_size + max(0, len(ascii_lines) - 1) * ascii_line_height
    )
    ascii_x = left_x + max(0.0, (available_ascii_width - ascii_block_width) / 2)
    ascii_first_y = max(
        ascii_vertical_padding + ascii_font_size,
        (height - ascii_block_height) / 2 + ascii_font_size,
    )

    title = resolve(config["title"], values)
    header = title + " " + "—" * max(3, content_width - len(title) - 1)
    output = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            "font-family=\"ConsolasFallback,Consolas,'Liberation Mono',monospace\" "
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'font-size="{font_size}">'
        ),
        "  <style>",
        "    @font-face {",
        "      src: local('Consolas'), local('Consolas Bold');",
        "      font-family: 'ConsolasFallback';",
        "      font-display: swap;",
        "      -webkit-size-adjust: 109%;",
        "      size-adjust: 109%;",
        "    }",
        f'    .key {{ fill: {theme["key"]}; }}',
        f'    .value {{ fill: {theme["value"]}; }}',
        f'    .muted {{ fill: {theme["muted"]}; }}',
        "    text, tspan { white-space: pre; }",
        "  </style>",
        f'  <rect width="{width}" height="{height}" fill="{theme["background"]}" rx="15"/>',
        (
            f'  <text x="{ascii_x:.2f}" y="{ascii_first_y:.2f}" '
            f'font-size="{ascii_font_size:.2f}" fill="{theme["text"]}" '
            'class="ascii" xml:space="preserve">'
        ),
    ]

    for index, line in enumerate(ascii_lines):
        y = ascii_first_y + ascii_line_height * index
        output.append(
            f'    <tspan x="{ascii_x:.2f}" y="{y:.2f}">{escaped(line)}</tspan>'
        )
    output.extend(
        [
            "  </text>",
            (
                f'  <text x="{right_x}" y="{first_y}" fill="{theme["text"]}" '
                'xml:space="preserve">'
            ),
            f'    <tspan x="{right_x}" y="{first_y}">{escaped(header)}</tspan>',
        ]
    )

    for index, row in enumerate(rows, start=1):
        y = first_y + line_height * index
        content = render_row(row, values, content_width, pair_left_width)
        output.append(f'    <tspan x="{right_x}" y="{y}">{content}</tspan>')

    output.extend(["  </text>", "</svg>", ""])
    return "\n".join(output)


def render_readme(config: dict[str, Any]) -> str:
    username = config["username"]
    branch = config["branch"]
    themes = {theme["name"]: theme for theme in config["themes"]}
    dark_file = themes["dark"]["file"]
    light_file = themes["light"]["file"]
    raw_base = f"https://raw.githubusercontent.com/{username}/{username}/{branch}"
    image_link = html.escape(config["image_link"], quote=True)
    alt_text = html.escape(config["alt_text"], quote=True)

    output = [
        "<!-- Bu dosya profile.config.json kullanılarak otomatik üretilir. -->",
        '<div align="center">',
        f'  <a href="{image_link}">',
        "    <picture>",
        (
            '      <source media="(prefers-color-scheme: dark)" '
            f'srcset="{raw_base}/{dark_file}">'
        ),
        f'      <img alt="{alt_text}" src="{raw_base}/{light_file}" width="100%">',
        "    </picture>",
        "  </a>",
        "</div>",
    ]

    visible_contacts = [
        contact for contact in config.get("contacts", []) if contact.get("show_below_card", False)
    ]
    if visible_contacts:
        output.extend(["", '<p align="center">'])
        for index, contact in enumerate(visible_contacts):
            separator = " ·" if index < len(visible_contacts) - 1 else ""
            url = html.escape(contact["url"], quote=True)
            label = escaped(contact.get("link_text", contact["label"]))
            output.append(f'  <a href="{url}">{label}</a>{separator}')
        output.append("</p>")

    output.append("")
    return "\n".join(output)


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = ("username", "branch", "title", "layout", "themes", "rows")
    missing = [name for name in required if name not in config]
    if missing:
        raise KeyError(f"Missing required config keys: {', '.join(missing)}")
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    config_path = args.config.resolve()
    config = load_config(config_path)
    rows = expand_rows(config)
    ascii_path = (config_path.parent / config["ascii_file"]).resolve()
    ascii_options = config.get("ascii", {})
    ascii_lines = prepare_ascii(
        ascii_path.read_text(encoding="utf-8").splitlines(),
        bool(ascii_options.get("trim_empty_margins", True)),
    )

    placeholder_source = json.dumps(
        {"rows": rows, "contacts": config.get("contacts", [])}, ensure_ascii=False
    )
    values = github_values(config["username"]) if "{{github." in placeholder_source else {}

    for theme in config["themes"]:
        output_path = ROOT / Path(theme["file"]).name
        output_path.write_text(
            render_svg(config, theme, rows, ascii_lines, values),
            encoding="utf-8",
            newline="\n",
        )

    (ROOT / "README.md").write_text(
        render_readme(config), encoding="utf-8", newline="\n"
    )
    print(
        f"Generated README.md and {len(config['themes'])} SVG card(s) "
        f"from {config_path.name}."
    )


if __name__ == "__main__":
    main()
