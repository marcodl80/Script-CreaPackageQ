import re
import shutil
from dataclasses import dataclass
from pathlib import Path

SOURCE_DIR = r"C:\SVILUPPO\SERVIZI_DA_CONVERTIRE"
DEST_DIR = r"C:\SVILUPPO\SERVIZI_CONVERTITI"
TEMPLATE_DIR = r"C:\SVILUPPO\TEMPLATEQ"
MODULE_DIR = r"C:\SVILUPPO\PACKAGES"

MAXIDHELP = 999411772  # first generated Id_Help will be MAXIDHELP + 1

PREFIXES = ("LSRESYNC", "LSINT", "LSW", "LS")
EXTS = {".pks", ".pkb"}

TRAILER_LOOKAHEAD = r"(?=[\.\(;'\x20])"  # . ( ; ' spazio

_METHOD_START_RX = re.compile(r"(?im)^[ \t]*(PROCEDURE|FUNCTION)[ \t]+([A-Z0-9_#$]+)\b")
_BEGIN_TOKEN_RX = re.compile(r"(?i)\bBEGIN\b")


def detect_prefix_and_nometabella(stem: str) -> tuple[str | None, str | None]:
    upper = stem.upper()
    for p in PREFIXES:
        if upper.startswith(p):
            return p, stem[len(p):]
    return None, None


def read_text_best_effort(path: Path) -> tuple[str, str]:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=enc), enc
        except UnicodeDecodeError:
            continue
    raw_bytes = path.read_bytes()
    return raw_bytes.decode("latin-1"), "latin-1"


def write_text(path: Path, content: str, encoding: str):
    path.write_text(content, encoding=encoding)


def build_replacements(nometabella: str):
    escaped = re.escape(nometabella)
    trailer = TRAILER_LOOKAHEAD
    return [
        (re.compile(rf"(?i)\bLSRESYNC{escaped}{trailer}"), f"LSRESYNC{nometabella}_Q"),
        (re.compile(rf"(?i)\bLSINT{escaped}{trailer}"), f"LSINT{nometabella}_Q"),
        (re.compile(rf"(?i)\bLSW{escaped}{trailer}"), f"LSW{nometabella}_Q"),
        (re.compile(rf"(?i)\bLS{escaped}{trailer}"), f"LS{nometabella}_Q"),
    ]


def transform_content_identifiers(content: str, nometabella: str) -> str:
    for rx, repl in build_replacements(nometabella):
        content = rx.sub(repl, content)
    return content


def template_basename_for_prefix(prefix: str) -> str:
    p = prefix.upper()
    if p == "LS":
        return "LsNOMETABELLA_Q"
    if p == "LSINT":
        return "LsIntNOMETABELLA_Q"
    if p == "LSW":
        return "LsWNOMETABELLA_Q"
    if p == "LSRESYNC":
        return "LsResyncNOMETABELLA_Q"
    raise ValueError(f"Unsupported prefix: {prefix}")


def find_generic_template_file(prefix: str, ext: str) -> Path | None:
    tdir = Path(TEMPLATE_DIR)
    if not tdir.exists():
        return None
    base = template_basename_for_prefix(prefix)
    wanted_upper = f"{base}{ext}".upper()
    for p in tdir.iterdir():
        if p.is_file() and p.suffix.lower() == ext.lower() and p.name.upper() == wanted_upper:
            return p
    return None


def list_methods(content: str) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for m in _METHOD_START_RX.finditer(content):
        found.add((m.group(1).upper(), m.group(2).upper()))
    return found


def _find_method_header_pos(text: str, kind: str, name: str) -> re.Match | None:
    rx = re.compile(rf"(?im)^[ \t]*{re.escape(kind)}[ \t]+{re.escape(name)}\b")
    return rx.search(text)


def _find_signature_paren_index(text: str, kind: str, name: str) -> int:
    rx = re.compile(rf"(?is){re.escape(kind)}\s+{re.escape(name)}\s*\(")
    m = rx.search(text)
    if not m:
        return -1
    return m.end() - 1


def _find_matching_paren_span(text: str, open_paren_index: int) -> tuple[int, int] | None:
    assert text[open_paren_index] == "("
    depth = 0
    in_str = False
    i = open_paren_index
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue
        if in_str:
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return open_paren_index, i
        i += 1
    return None


def _strip_line_comment_outside_quotes(line: str) -> str:
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'":
            if i + 1 < len(line) and line[i + 1] == "'":
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue
        if not in_str and ch == "-" and i + 1 < len(line) and line[i + 1] == "-":
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _clean_param_for_compare(param: str) -> str:
    lines = [_strip_line_comment_outside_quotes(ln) for ln in param.splitlines()]
    s = "\n".join(lines).strip()
    s = re.sub(r"[ \t]+", " ", s)
    return s


def _split_params(param_block: str) -> list[str]:
    params = []
    start = 0
    depth = 0
    in_str = False
    i = 0
    while i < len(param_block):
        ch = param_block[i]
        if ch == "'":
            if i + 1 < len(param_block) and param_block[i + 1] == "'":
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue
        if in_str:
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            item = param_block[start:i].strip()
            if item:
                params.append(item)
            start = i + 1
        i += 1
    last = param_block[start:].strip()
    if last:
        params.append(last)
    return params


def _param_name(param: str) -> str | None:
    s = _clean_param_for_compare(param)
    m = re.match(r"(?is)^\s*([A-Z0-9_#$]+)\b", s)
    return m.group(1).upper() if m else None


def _format_param_list(params: list[str], indent: str) -> str:
    if not params:
        return ""
    lines = []
    for idx, p in enumerate(params):
        comma = "," if idx < len(params) - 1 else ""
        lines.append(f"{indent}{p}{comma}")
    return "\n" + "\n".join(lines) + "\n"


def _merge_params(template_params: list[str], target_params: list[str]) -> tuple[list[str], list[str]]:
    existing_names = {_param_name(p) for p in target_params if _param_name(p)}
    tpl_pairs = [(_param_name(p), p) for p in template_params]
    tpl_pairs = [(n, p) for (n, p) in tpl_pairs if n]

    result = list(target_params)
    added_names: list[str] = []

    def find_last_index_of_any(names: set[str]) -> int:
        for i in range(len(result) - 1, -1, -1):
            rn = _param_name(result[i])
            if rn in names:
                return i
        return -1

    seen_tpl_names: list[str] = []
    for tpl_name, tpl_param in tpl_pairs:
        if tpl_name in existing_names:
            seen_tpl_names.append(tpl_name)
            continue
        prev_set = set(seen_tpl_names)
        insert_after = find_last_index_of_any(prev_set) if prev_set else -1
        insert_pos = insert_after + 1
        result.insert(insert_pos, tpl_param)
        added_names.append(tpl_name)
        existing_names.add(tpl_name)
        seen_tpl_names.append(tpl_name)

    return result, added_names


def _dedupe_and_clean_params(params: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in params:
        name = _param_name(p)
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(p)
    return out


def _apply_lsresync_getsql_searchparam_rule(
    resolved_method_name: str,
    nometabella: str,
    out_params_before_merge: list[str],
    merged_params: list[str],
) -> tuple[list[str], bool]:
    if resolved_method_name.upper() != f"GETSQL{nometabella.upper()}":
        return merged_params, False

    before_names = {_param_name(p) for p in out_params_before_merge if _param_name(p)}
    if "P_SEARCHPARAM" in before_names:
        return merged_params, False

    remove = {"P_FILTER", "P_ORDERBYCOND", "P_SCOPENAME"}
    result = [p for p in merged_params if (_param_name(p) or "") not in remove]

    wanted = f"p_SearchParam     {nometabella.upper()}_SEARCH_PARAM"
    names = [(_param_name(p) or "") for p in result]

    if "P_SEARCHPARAM" in names:
        result2 = []
        for p in result:
            if (_param_name(p) or "") == "P_SEARCHPARAM":
                result2.append(wanted)
            else:
                result2.append(p)
        result = result2
    else:
        anchors = ["P_TIPOSQL", "P_CONTEXT", "P_BO_SESSIONID", "P_CODCOMPANY"]
        insert_at = len(result)
        for a in anchors:
            if a in names:
                insert_at = names.index(a) + 1
                break
        result.insert(insert_at, wanted)

    result = _dedupe_and_clean_params(result)
    return result, True


# -----------------------------------------------------------------------------
# Outer-BEGIN finder: salta FUNCTION/PROCEDURE annidate
# -----------------------------------------------------------------------------
def _find_outer_begin(text: str, after_pos: int) -> re.Match | None:
    scan_rx = re.compile(
        r"(?im)"
        r"(?:^[ \t]*(?:FUNCTION|PROCEDURE)[ \t]+[A-Z0-9_#$]+\b)"
        r"|"
        r"(?:\bBEGIN\b)"
    )
    begin_end_rx = re.compile(r"(?i)\bBEGIN\b|\bEND\b[ \t]*;")

    pos = after_pos
    while True:
        m = scan_rx.search(text, pos=pos)
        if not m:
            return None

        token = m.group(0).strip().upper()
        if token == "BEGIN":
            return m

        inner_begin = re.compile(r"(?i)\bBEGIN\b").search(text, pos=m.end())
        if not inner_begin:
            return None

        depth = 1
        found = False
        for em in begin_end_rx.finditer(text, pos=inner_begin.end()):
            tok = em.group(0).upper().strip()
            if tok == "BEGIN":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    pos = em.end()
                    found = True
                    break
        if not found:
            return None


def extract_method_block_body(text: str, kind: str, name: str) -> str | None:
    sm = _find_method_header_pos(text, kind, name)
    if not sm:
        return None
    start = sm.start()

    begin_m = _find_outer_begin(text, sm.end())
    if not begin_m:
        return None

    token_rx = re.compile(r"(?i)\bBEGIN\b|\bEND\b[ \t]*;")
    depth = 0
    end_pos = None
    for tm in token_rx.finditer(text, pos=begin_m.start()):
        tok = tm.group(0).upper().strip()
        if tok == "BEGIN":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                end_pos = tm.end()
                break
    if end_pos is None:
        return None

    return text[start:end_pos].rstrip() + "\n"


def insert_blocks_before_package_end(pkg_content: str, blocks: list[str]) -> str:
    if not blocks:
        return pkg_content
    end_pkg_rx = re.compile(r"(?im)^[ \t]*END[ \t]+[A-Z0-9_#$]+[ \t]*;[ \t]*$")
    matches = list(end_pkg_rx.finditer(pkg_content))
    insertion = "\n\n" + "\n\n".join(b.strip("\n") for b in blocks).rstrip() + "\n\n"
    if matches:
        last = matches[-1]
        return pkg_content[: last.start()] + insertion + pkg_content[last.start():]
    return pkg_content.rstrip() + insertion


def _insert_after_first_regex(text: str, pattern: re.Pattern, insert_text: str) -> tuple[str, bool]:
    m = pattern.search(text)
    if not m:
        return text, False
    pos = m.end()
    new_text = text[:pos] + "\n" + insert_text + text[pos:]
    return new_text, (new_text != text)


# ------------------------------------------------------------------------------------
# SEARCHOBJECTS.txt parsing + helpers
# ------------------------------------------------------------------------------------
def _extract_balanced_parens(text: str, open_idx: int) -> tuple[int, int] | None:
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "(":
        return None
    depth = 0
    in_str = False
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue
        if in_str:
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return open_idx, i
        i += 1
    return None


_SEARCHOBJECTS_CACHE: dict[str, list[tuple[str, str]]] | None = None


def _load_searchobjects_cache() -> dict[str, list[tuple[str, str]]]:
    global _SEARCHOBJECTS_CACHE
    if _SEARCHOBJECTS_CACHE is not None:
        return _SEARCHOBJECTS_CACHE

    cache: dict[str, list[tuple[str, str]]] = {}
    p = Path(SOURCE_DIR) / "SEARCHOBJECTS.txt"
    if not p.exists():
        _SEARCHOBJECTS_CACHE = cache
        return cache

    text, _enc = read_text_best_effort(p)

    hdr_rx = re.compile(
        r"(?is)CREATE\s+OR\s+REPLACE\s+TYPE\s+([A-Z0-9_#$]+_SEARCH_PARAM)\b",
        re.IGNORECASE,
    )

    for m in hdr_rx.finditer(text):
        type_name = m.group(1)
        start = m.start()
        chunk = text[start:start + 200000]

        as_obj = re.search(r"(?is)\bAS\s+OBJECT\b", chunk)
        if not as_obj:
            continue

        after_as = as_obj.end()
        open_idx_rel = chunk.find("(", after_as)
        if open_idx_rel < 0:
            continue

        span = _extract_balanced_parens(chunk, open_idx_rel)
        if not span:
            continue

        inside = chunk[span[0] + 1:span[1]]
        attrs: list[tuple[str, str]] = []

        for line in inside.splitlines():
            s = line.strip()
            if not s or s.startswith("--"):
                continue
            s = re.split(r"--", s, maxsplit=1)[0].strip()
            s = s.rstrip(",").strip()
            if not s:
                continue

            mm = re.match(r"(?is)^([A-Z0-9_#$]+)\s+(.+)$", s)
            if not mm:
                continue

            attr = mm.group(1).strip()
            typ = mm.group(2).strip()
            attrs.append((attr, typ))

        if attrs:
            table_upper = type_name.upper().replace("_SEARCH_PARAM", "")
            cache[table_upper] = attrs

    _SEARCHOBJECTS_CACHE = cache
    return cache


def _table_has_searchobjects(table_upper: str) -> bool:
    cache = _load_searchobjects_cache()
    return table_upper.upper() in cache


def _precision_for_type(attr_type_raw: str) -> str:
    t = attr_type_raw.strip().upper()
    if "VARCHAR2" in t or "DATE" in t or "DATETIME" in t:
        return "NULL"
    m = re.search(r"NUMBER\s*\(\s*(\d+)\s*\)", t)
    if m:
        n = m.group(1)
        if n == "9":
            return "(9)"
        if n == "1":
            return "(1)"
    return "NULL"


def _build_wherecond_blocks_for_searchparam(table: str) -> tuple[int, str]:
    cache = _load_searchobjects_cache()
    attrs = cache.get(table.upper(), [])
    num_fields = len(attrs)

    blocks = []
    for (attr, typ) in attrs:
        if attr.upper() in ("PFILTER", "PORDERBYCOND", "PSCOPENAME"):
            continue
        precision = _precision_for_type(typ)
        blocks.append(
            "  IF p_SearchParam.{attr} IS NOT NULL THEN\n"
            "      v_WhereCond := LsSql.AddCond(v_WhereCond, '{table}.{attr}', p_SearchParam.{attr}, LsDBConst.c_COMPARE_EQUAL, {precision});\n"
            "    END IF;\n".format(attr=attr, table=table.upper(), precision=precision)
        )

    return num_fields, "".join(blocks)


def _null_list_for_searchparam(table: str) -> tuple[int, str]:
    cache = _load_searchobjects_cache()
    attrs = cache.get(table.upper(), [])
    if not attrs:
        return 0, ""
    return len(attrs), ", ".join(["NULL"] * len(attrs))


def _plain_params_for_searchparam(table: str) -> list[tuple[str, str]]:
    cache = _load_searchobjects_cache()
    attrs = cache.get(table.upper(), [])
    out: list[tuple[str, str]] = []
    for (attr, typ) in attrs:
        if attr.upper() in ("PFILTER", "PORDERBYCOND", "PSCOPENAME"):
            continue
        t = typ.strip()
        m = re.match(r"(?is)^([A-Z0-9_#$]+)", t)
        if not m:
            continue
        base_type = m.group(1).upper()
        out.append((attr.upper(), base_type))
    return out


# ------------------------------------------------------------------------------------
# LSRESYNC multi-GetSql support
# ------------------------------------------------------------------------------------
def _iter_getsql_table_suffixes_in_text(text: str) -> list[str]:
    rx = re.compile(r"(?im)^[ \t]*FUNCTION[ \t]+GetSql([A-Z0-9_#$]+)\b")
    return [m.group(1).upper() for m in rx.finditer(text)]


def _slice_plsql_function_by_name(text: str, func_name_upper: str) -> tuple[int, int] | None:
    sm = _find_method_header_pos(text, "FUNCTION", func_name_upper)
    if not sm:
        return None
    start = sm.start()

    begin_m = _find_outer_begin(text, sm.end())
    if not begin_m:
        return None

    token_rx = re.compile(r"(?i)\bBEGIN\b|\bEND\b[ \t]*;")
    depth = 0
    end_pos = None
    for tm in token_rx.finditer(text, pos=begin_m.start()):
        tok = tm.group(0).upper().strip()
        if tok == "BEGIN":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                end_pos = tm.end()
                break
    if end_pos is None:
        return None
    return start, end_pos


def _extract_params_from_signature_in_text(text: str, kind: str, func_name_upper: str) -> list[str]:
    open_idx = _find_signature_paren_index(text, kind, func_name_upper)
    if open_idx < 0:
        return []
    span = _find_matching_paren_span(text, open_idx)
    if not span:
        return []
    inside = text[span[0] + 1:span[1]]
    return _split_params(inside)


def _extract_template_getsql_reference_params(template_text: str) -> list[str]:
    getsqls = _iter_getsql_table_suffixes_in_text(template_text)
    if not getsqls:
        return []

    func_name = f"GETSQL{getsqls[0]}"
    params = _extract_params_from_signature_in_text(template_text, "FUNCTION", func_name)
    if not params:
        return []

    names = [(_param_name(p) or "").upper() for p in params]
    tail_start = None
    for i, n in enumerate(names):
        if n == "P_WITHACTIONINFO":
            tail_start = i
            break
    return params[tail_start:] if tail_start is not None else []
