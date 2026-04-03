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


def _patch_lsresync_one_getsql_signature_using_template(
    resync_text: str,
    func_table_upper: str,
    template_tail_params: list[str],
) -> tuple[str, bool]:
    if not _table_has_searchobjects(func_table_upper):
        return resync_text, False

    func_name = f"GETSQL{func_table_upper}"
    open_idx = _find_signature_paren_index(resync_text, "FUNCTION", func_name)
    if open_idx < 0:
        return resync_text, False
    span = _find_matching_paren_span(resync_text, open_idx)
    if not span:
        return resync_text, False

    inside = resync_text[span[0] + 1:span[1]]
    params = _split_params(inside)

    filtered = []
    for p in params:
        nm = (_param_name(p) or "").upper()
        if nm in ("P_FILTER", "P_ORDERBYCOND", "P_SCOPENAME"):
            continue
        filtered.append(p)

    names = [(_param_name(p) or "").upper() for p in filtered]
    if "P_SEARCHPARAM" not in names:
        wanted = f"p_SearchParam    {func_table_upper}_SEARCH_PARAM"
        insert_at = None
        for i, n in enumerate(names):
            if n == "P_TIPOSQL":
                insert_at = i + 1
                break
        if insert_at is None:
            insert_at = len(filtered)
        filtered.insert(insert_at, wanted)

    if template_tail_params:
        existing_names = {(_param_name(p) or "").upper() for p in filtered}
        for tp in template_tail_params:
            tn = (_param_name(tp) or "").upper()
            if not tn:
                continue
            if tn not in existing_names:
                filtered.append(tp)
                existing_names.add(tn)

    filtered = _dedupe_and_clean_params(filtered)

    if [p.strip() for p in filtered] == [p.strip() for p in params]:
        return resync_text, False

    indent = "  "
    for line in inside.splitlines():
        if line.strip():
            indent = re.match(r"^\s*", line).group(0)
            break
    new_inside = _format_param_list(filtered, indent)
    out = resync_text[:span[0] + 1] + new_inside + resync_text[span[1]:]
    return out, True


def _getsql_has_searchparam_in_signature(text: str, table_upper: str) -> bool:
    func_name = f"GETSQL{table_upper}"
    open_idx = _find_signature_paren_index(text, "FUNCTION", func_name)
    if open_idx < 0:
        return False
    span = _find_matching_paren_span(text, open_idx)
    if not span:
        return False
    inside = text[span[0] + 1:span[1]]
    params = _split_params(inside)
    param_names = [(_param_name(p) or "").upper() for p in params]
    return "P_SEARCHPARAM" in param_names


def _patch_lsresync_replace_filter_order_scope_inside_getsql(resync_text: str, table_upper: str) -> tuple[str, bool]:
    func_name = f"GETSQL{table_upper}"

    if not _getsql_has_searchparam_in_signature(resync_text, table_upper):
        return resync_text, False

    sl = _slice_plsql_function_by_name(resync_text, func_name)
    if not sl:
        return resync_text, False
    i, j = sl
    chunk = resync_text[i:j]
    orig = chunk

    chunk = re.sub(r"(?is)\bp_filter\b", "p_SearchParam.pFilter", chunk)
    chunk = re.sub(r"(?is)\bp_orderbycond\b", "p_SearchParam.pOrderByCond", chunk)
    chunk = re.sub(r"(?is)\bp_scopename\b", "p_SearchParam.pScopeName", chunk)

    if chunk == orig:
        return resync_text, False
    return resync_text[:i] + chunk + resync_text[j:], True


def _find_end_of_v_wherecond_assignment_in_list_branch_within_chunk(chunk: str) -> int | None:
    m_list = re.search(r"(?is)\bif\s+p_TipoSQL\s*=\s*SiwFunc\s*\.\s*c_List\s+then\b", chunk)
    if not m_list:
        return None

    m_assign = re.search(r"(?is)\bv_WhereCond\s*:=\s*'", chunk[m_list.end():])
    if not m_assign:
        return None

    start = m_list.end() + m_assign.start()

    in_str = False
    i = start
    while i < len(chunk):
        ch = chunk[i]
        if ch == "'":
            if i + 1 < len(chunk) and chunk[i + 1] == "'":
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue
        if not in_str and ch == ";":
            return i + 1
        i += 1
    return None


def _patch_lsresync_inject_addcond_inside_getsql(resync_text: str, table_upper: str) -> tuple[str, bool]:
    if not _table_has_searchobjects(table_upper):
        return resync_text, False

    func_name = f"GETSQL{table_upper}"
    sl = _slice_plsql_function_by_name(resync_text, func_name)
    if not sl:
        return resync_text, False
    i, j = sl
    chunk = resync_text[i:j]

    num_fields, blocks = _build_wherecond_blocks_for_searchparam(table_upper)
    if num_fields == 0 or not blocks.strip():
        return resync_text, False

    if re.search(r"(?is)IF\s+p_SearchParam\.", chunk):
        return resync_text, False

    end_pos = _find_end_of_v_wherecond_assignment_in_list_branch_within_chunk(chunk)
    if end_pos is not None:
        new_chunk = chunk[:end_pos] + "\n" + blocks.rstrip("\n") + chunk[end_pos:]
        if new_chunk == chunk:
            return resync_text, False
        return resync_text[:i] + new_chunk + resync_text[j:], True

    stmt_rx = re.compile(
        r"(?im)^[ \t]*v_Result[ \t]*:=[ \t]*LsGeneric[ \t]*\.[ \t]*GetSqlView[ \t]*\([ \t]*c_VIEW_NAME[ \t]*\)[ \t]*;[ \t]*$"
    )
    m = stmt_rx.search(chunk)
    if not m:
        return resync_text, False

    pos = m.end()
    new_chunk = chunk[:pos] + "\n" + blocks.rstrip("\n") + chunk[pos:]
    if new_chunk == chunk:
        return resync_text, False
    return resync_text[:i] + new_chunk + resync_text[j:], True


def patch_lsresync_scopes_call_to_searchparam(text: str) -> tuple[str, int]:
    rx = re.compile(r"(?is)\bLsScopes\s*\.\s*GetWhereCondByScopeNames\s*\(")
    out = text
    pos = 0
    changed = 0

    while True:
        m = rx.search(out, pos)
        if not m:
            break

        open_idx = m.end() - 1
        span = _find_matching_paren_span(out, open_idx)
        if not span:
            break

        inside = out[span[0] + 1:span[1]]
        args = _split_params(inside)

        if len(args) >= 5:
            a3 = args[3].strip()
            if re.fullmatch(r"(?is)p_scopename", a3):
                args[3] = "p_SearchParam.pScopeName"

                rebuilt_lines = [f"                                              {a}," for a in args[:-1]] + [
                    f"                                              {args[-1]}"
                ]
                rebuilt = "\n" + "\n".join(rebuilt_lines) + "\n"

                out = out[:span[0] + 1] + rebuilt + out[span[1]:]
                changed += 1
                pos = span[0] + 1 + len(rebuilt) + 1
                continue

        pos = span[1] + 1

    return out, changed


def _patch_lsresync_scopes_call_inside_getsql_if_allowed(resync_text: str, table_upper: str) -> tuple[str, bool]:
    if not _getsql_has_searchparam_in_signature(resync_text, table_upper):
        return resync_text, False

    func_name = f"GETSQL{table_upper}"
    sl = _slice_plsql_function_by_name(resync_text, func_name)
    if not sl:
        return resync_text, False
    i, j = sl
    chunk = resync_text[i:j]
    new_chunk, cnt = patch_lsresync_scopes_call_to_searchparam(chunk)
    if cnt <= 0:
        return resync_text, False
    return resync_text[:i] + new_chunk + resync_text[j:], True


# ------------------------------------------------------------------------------------
# capture p_ME_ params from generated LSRESYNC*_Q.pks
# ------------------------------------------------------------------------------------
_RESYNC_TABLE_ME_PARAMS: dict[str, list[str]] = {}
_RESYNC_TABLE_GETSQL_PARAM_ORDER: dict[str, list[str]] = {}
_RESYNC_TABLES_WITH_SEARCHPARAM: set[str] = set()


def _extract_lsresync_getsql_me_params_from_pks_text(pks_text: str, table_upper: str) -> list[str]:
    func_rx = re.compile(rf"(?is)\bFUNCTION\s+GetSql{re.escape(table_upper)}\b")
    m = func_rx.search(pks_text)
    if not m:
        return []

    open_idx = pks_text.find("(", m.end())
    if open_idx < 0:
        return []

    span = _find_matching_paren_span(pks_text, open_idx)
    if not span:
        return []

    inside = pks_text[span[0] + 1:span[1]]
    params = _split_params(inside)

    me_params: list[str] = []
    for p in params:
        name = _param_name(p)
        if not name:
            continue
        if name.upper().startswith("P_ME_"):
            me_params.append(p.strip())

    return me_params


def _extract_lsresync_getsql_param_order_from_pks_text(pks_text: str, table_upper: str) -> list[str]:
    func_rx = re.compile(rf"(?is)\bFUNCTION\s+GetSql{re.escape(table_upper)}\b")
    m = func_rx.search(pks_text)
    if not m:
        return []

    open_idx = pks_text.find("(", m.end())
    if open_idx < 0:
        return []

    span = _find_matching_paren_span(pks_text, open_idx)
    if not span:
        return []

    inside = pks_text[span[0] + 1:span[1]]
    params = _split_params(inside)

    order: list[str] = []
    for p in params:
        n = _param_name(p)
        if n:
            order.append(n.upper())
    return order


def _capture_lsresync_me_params_for_table(dest_dir: Path, table_upper: str):
    pks_path = dest_dir / f"LsResync{table_upper}_Q.pks"
    if not pks_path.exists():
        return

    text, _enc = read_text_best_effort(pks_path)

    order = _extract_lsresync_getsql_param_order_from_pks_text(text, table_upper)
    if order:
        _RESYNC_TABLE_GETSQL_PARAM_ORDER[table_upper] = order

    me_params = _extract_lsresync_getsql_me_params_from_pks_text(text, table_upper)
    if me_params:
        _RESYNC_TABLE_ME_PARAMS[table_upper] = me_params


# ------------------------------------------------------------------------------------
# Cross-package injection helpers
# ------------------------------------------------------------------------------------
def _inject_me_params_after_anchor_in_signature_anykind(
    text: str,
    kind: str,
    table_upper: str,
    me_params: list[str],
    anchor_param_upper: str,
) -> tuple[str, bool]:
    name = f"LS_SEARCH_{table_upper}"
    hdr = _find_method_header_pos(text, kind, name)
    if not hdr:
        return text, False

    open_idx = text.find("(", hdr.end())
    if open_idx < 0:
        return text, False

    span = _find_matching_paren_span(text, open_idx)
    if not span:
        return text, False

    inside = text[span[0] + 1:span[1]]
    params = _split_params(inside)
    if not params:
        return text, False

    idx_anchor = next((i for i, p in enumerate(params) if (_param_name(p) or "") == anchor_param_upper), None)
    if idx_anchor is None:
        return text, False

    existing = {(_param_name(p) or "") for p in params}
    to_add = []
    for p in me_params:
        n = _param_name(p) or ""
        if n and n not in existing:
            to_add.append(p)

    if not to_add:
        return text, False

    new_params = params[: idx_anchor + 1] + to_add + params[idx_anchor + 1:]

    indent = "  "
    for line in inside.splitlines():
        if line.strip():
            indent = re.match(r"^\s*", line).group(0)
            break

    new_inside = _format_param_list(new_params, indent)
    out = text[:span[0] + 1] + new_inside + text[span[1]:]
    return out, (out != text)


def _inject_me_params_into_getrow_getrecord_getlist_signatures(
    text: str,
    table_upper: str,
    me_params: list[str],
    kind: str = "FUNCTION",
) -> tuple[str, bool]:
    """Insert p_ME_* params (after p_Context) into GetRow*, GetList*, GetRecord* signatures.

    This mirrors what the _Q GetSql signature requires: the callers must declare the same
    p_ME_* parameters so they can be forwarded to GetSql.
    """
    changed = False
    for method_name in (
        f"GETROW{table_upper}",
        f"GETLIST{table_upper}",
        f"GETRECORD{table_upper}",
    ):
        hdr = _find_method_header_pos(text, kind, method_name)
        if not hdr:
            continue

        open_idx = text.find("(", hdr.end())
        if open_idx < 0:
            continue

        span = _find_matching_paren_span(text, open_idx)
        if not span:
            continue

        inside = text[span[0] + 1 : span[1]]
        params = _split_params(inside)
        if not params:
            continue

        # Anchor: inject after p_Context.
        idx_anchor = next(
            (i for i, p in enumerate(params) if (_param_name(p) or "").upper() == "P_CONTEXT"),
            None,
        )
        if idx_anchor is None:
            continue

        existing = {(_param_name(p) or "").upper() for p in params}
        to_add = [mp.strip() for mp in me_params
                  if (name := (_param_name(mp) or "")) and name.upper() not in existing]
        if not to_add:
            continue

        new_params = params[: idx_anchor + 1] + to_add + params[idx_anchor + 1 :]

        indent = "  "
        for line in inside.splitlines():
            if line.strip():
                indent = re.match(r"^\s*", line).group(0)
                break

        new_inside = _format_param_list(new_params, indent)
        text = text[: span[0] + 1] + new_inside + text[span[1] :]
        changed = True

    return text, changed


def _find_searchparam_arg_index(args: list[str]) -> int | None:
    for i, a in enumerate(args):
        s = a.strip()
        up = s.upper()
        if up in ("P_SEARCHPARAM", "V_SEARCHPARAM"):
            return i
        if re.fullmatch(r"(?is)[A-Z0-9_#$\.]*SEARCHPARAM", s):
            return i
    return None


def _inject_me_params_into_ls_search_call_after_searchparam_anykind(
    text: str,
    kind: str,
    table_upper: str,
    me_params: list[str],
    callee_package_prefix: str,
) -> tuple[str, bool]:
    name = f"LS_SEARCH_{table_upper}"
    mb = extract_method_block_body(text, kind, name)
    if not mb:
        return text, False

    out_mb = mb

    call_rx = re.compile(
        rf"(?is)({re.escape(callee_package_prefix)}{re.escape(table_upper)}_Q\s*\.\s*LS_SEARCH_{re.escape(table_upper)}\s*\()"
    )
    cm = call_rx.search(out_mb)
    if not cm:
        return text, False

    open_idx = cm.end() - 1
    span = _find_matching_paren_span(out_mb, open_idx)
    if not span:
        return text, False

    inside = out_mb[span[0] + 1:span[1]]
    args = _split_params(inside)
    if not args:
        return text, False

    idx_sp = _find_searchparam_arg_index(args)

    me_arg_names: list[str] = []
    for p in me_params:
        mm = re.match(r"(?is)^\s*([A-Z0-9_#$]+)", p.strip())
        if mm:
            me_arg_names.append(mm.group(1))

    _OLD_STYLE_REMOVE = {"P_FILTER", "P_ORDERBYCOND", "P_SCOPENAME"}
    has_old_style = any(a.strip().upper() in _OLD_STYLE_REMOVE for a in args)

    if idx_sp is None and has_old_style:
        # Old-style call (uses p_Filter/p_OrderByCond/p_ScopeName instead of p_SearchParam):
        # remove the old filter params and insert p_SearchParam right after p_Context.
        args_filtered = [a for a in args if a.strip().upper() not in _OLD_STYLE_REMOVE]
        idx_ctx = next(
            (i for i, a in enumerate(args_filtered) if a.strip().upper() == "P_CONTEXT"), None
        )
        if idx_ctx is None:
            return text, False
        new_args = args_filtered[: idx_ctx + 1] + ["p_SearchParam"] + args_filtered[idx_ctx + 1:]
        # If any me_params are not yet present, insert them after p_SearchParam.
        existing_args_upper = {a.strip().upper() for a in new_args}
        to_add_me = [a for a in me_arg_names if a.upper() not in existing_args_upper]
        if to_add_me:
            idx_searchparam_converted = _find_searchparam_arg_index(new_args)
            if idx_searchparam_converted is not None:
                new_args = new_args[: idx_searchparam_converted + 1] + to_add_me + new_args[idx_searchparam_converted + 1:]
            else:
                new_args = new_args + to_add_me
    elif idx_sp is None:
        return text, False
    else:
        # New-style call: p_SearchParam already present; add any missing me_params after it.
        existing_upper = {a.strip().upper() for a in args}
        to_add = [a for a in me_arg_names if a.upper() not in existing_upper]
        if not to_add:
            return text, False
        new_args = args[: idx_sp + 1] + to_add + args[idx_sp + 1:]

    if [a.strip() for a in new_args] == [a.strip() for a in args]:
        return text, False

    rebuilt_lines = [f"      {a}," for a in new_args[:-1]] + [f"      {new_args[-1]}"]
    rebuilt = "\n" + "\n".join(rebuilt_lines) + "\n"

    out_mb2 = out_mb[:span[0] + 1] + rebuilt + out_mb[span[1]:]
    out_text = text.replace(mb, out_mb2)
    return out_text, (out_text != text)


def _inject_me_params_into_ls_search_call_getsql_lsresync(text: str, table_upper: str, me_params: list[str]) -> tuple[str, bool]:
    func_name = f"LS_SEARCH_{table_upper}"
    mb = extract_method_block_body(text, "FUNCTION", func_name)
    if not mb:
        return text, False

    out_mb = mb

    call_rx = re.compile(
        rf"(?is)(LSRESYNC{re.escape(table_upper)}_Q\s*\.\s*GetSql{re.escape(table_upper)}\s*\()"
    )
    cm = call_rx.search(out_mb)
    if not cm:
        return text, False

    open_idx = cm.end() - 1
    span = _find_matching_paren_span(out_mb, open_idx)
    if not span:
        return text, False

    inside = out_mb[span[0] + 1:span[1]]
    args = _split_params(inside)
    if not args:
        return text, False

    sig_order = _RESYNC_TABLE_GETSQL_PARAM_ORDER.get(table_upper, [])
    if not sig_order:
        return text, False

    me_arg_names: list[str] = []
    for p in me_params:
        mm = re.match(r"(?is)^\s*([A-Z0-9_#$]+)", p.strip())
        if mm:
            me_arg_names.append(mm.group(1))
    me_arg_names_upper = [x.upper() for x in me_arg_names]
    me_set_upper = set(me_arg_names_upper)

    idx_context = next((i for i, a in enumerate(args) if a.strip().upper() == "P_CONTEXT"), None)
    idx_searchparam = next((i for i, a in enumerate(args) if a.strip().upper() == "P_SEARCHPARAM"), None)

    if idx_context is None:
        return text, False

    _OLD_STYLE_REMOVE = {"P_FILTER", "P_ORDERBYCOND", "P_SCOPENAME"}
    has_old_style = any(a.strip().upper() in _OLD_STYLE_REMOVE for a in args)

    if idx_searchparam is None and has_old_style:
        # Old-style call: the body passes p_Filter/p_OrderByCond/p_ScopeName to GetSql
        # but the generated LsResync*_Q.GetSql* now requires p_SearchParam.
        # Convert by removing the old filter params and inserting p_SearchParam after
        # p_Context; keep me_params (already present from original) in their positions.
        args_filtered = [a for a in args if a.strip().upper() not in _OLD_STYLE_REMOVE]
        idx_ctx_f = next(
            (i for i, a in enumerate(args_filtered) if a.strip().upper() == "P_CONTEXT"), None
        )
        if idx_ctx_f is None:
            return text, False
        new_args = args_filtered[: idx_ctx_f + 1] + ["p_SearchParam"] + args_filtered[idx_ctx_f + 1:]

    elif idx_searchparam is None:
        return text, False

    else:
        # New-style call: p_SearchParam is already present; rearrange me_params.
        if idx_context + 1 >= len(args):
            return text, False

        tipo_sql_arg = args[idx_context + 1]

        me_in_sig_order: list[str] = []
        for pname in sig_order:
            if pname.startswith("P_ME_") and pname in me_set_upper:
                ix = me_arg_names_upper.index(pname)
                me_in_sig_order.append(me_arg_names[ix])

        prefix = args[: idx_context + 1]
        tail = args[idx_searchparam:]
        tail_filtered = [a for a in tail if a.strip().upper() not in me_set_upper]

        new_args = prefix + me_in_sig_order + [tipo_sql_arg] + tail_filtered

    if [a.strip() for a in new_args] == [a.strip() for a in args]:
        return text, False

    rebuilt_lines = [f"      {a}," for a in new_args[:-1]] + [f"      {new_args[-1]}"]
    rebuilt = "\n" + "\n".join(rebuilt_lines) + "\n"

    out_mb2 = out_mb[:span[0] + 1] + rebuilt + out_mb[span[1]:]
    out_text = text.replace(mb, out_mb2)
    return out_text, (out_text != text)


def patch_lsw_pkb_searchparam_constructor_null_list(text: str, table_upper: str) -> tuple[str, int]:
    n, null_list = _null_list_for_searchparam(table_upper)
    if n <= 0 or not null_list.strip():
        return text, 0

    name = f"LS_SEARCH_{table_upper}"
    mb = extract_method_block_body(text, "PROCEDURE", name)
    if not mb:
        return text, 0

    out_mb = mb
    rx = re.compile(rf"(?is)\bv_SearchParam\s*:=\s*{re.escape(table_upper)}_SEARCH_PARAM\s*\(")
    m = rx.search(out_mb)
    if not m:
        return text, 0

    open_idx = m.end() - 1
    span = _find_matching_paren_span(out_mb, open_idx)
    if not span:
        return text, 0

    new_inside = " " + null_list + " "
    out_mb2 = out_mb[:span[0] + 1] + new_inside + out_mb[span[1]:]
    if out_mb2 == out_mb:
        return text, 0

    out_text = text.replace(mb, out_mb2)
    return out_text, 1


def _patch_lsw_ls_search_signature_add_fields_from_searchparam(
    text: str,
    table_upper: str,
) -> tuple[str, bool]:
    name = f"LS_SEARCH_{table_upper}"
    hdr = _find_method_header_pos(text, "PROCEDURE", name)
    if not hdr:
        return text, False

    open_idx = text.find("(", hdr.end())
    if open_idx < 0:
        return text, False

    span = _find_matching_paren_span(text, open_idx)
    if not span:
        return text, False

    inside = text[span[0] + 1:span[1]]
    params = _split_params(inside)
    if not params:
        return text, False

    idx_with = next(
        (i for i, p in enumerate(params) if (_param_name(p) or "").upper() == "P_WITHACTIONINFO"),
        None,
    )
    if idx_with is None:
        return text, False

    plain_params = _plain_params_for_searchparam(table_upper)
    if not plain_params:
        return text, False

    existing_names = {(_param_name(p) or "").upper() for p in params}
    to_insert: list[str] = []
    for (attr, base_type) in plain_params:
        if attr.upper() in existing_names:
            continue
        to_insert.append(f"{attr:<12} {base_type}")

    if not to_insert:
        return text, False

    new_params = params[:idx_with] + to_insert + params[idx_with:]

    indent = "  "
    for line in inside.splitlines():
        if line.strip():
            indent = re.match(r"^\s*", line).group(0)
            break

    new_inside = _format_param_list(new_params, indent)
    out = text[: span[0] + 1] + new_inside + text[span[1] :]
    return out, (out != text)


def _patch_lsw_pkb_searchparam_assignments(text: str, table_upper: str) -> tuple[str, bool]:
    name = f"LS_SEARCH_{table_upper}"
    mb = extract_method_block_body(text, "PROCEDURE", name)
    if not mb:
        return text, False

    out_mb = mb

    scope_rx = re.compile(r"(?im)^\s*v_SearchParam\s*\.\s*pScopeName\s*:=\s*pScopeName\s*;\s*$")
    m = scope_rx.search(out_mb)
    if not m:
        return text, False

    insert_pos = m.end()
    plain_params = _plain_params_for_searchparam(table_upper)
    if not plain_params:
        return text, False

    existing_assign_rx = re.compile(
        r"(?im)^\s*v_SearchParam\s*\.\s*([A-Z0-9_#$]+)\s*:=\s*([A-Z0-9_#$]+)\s*;\s*$"
    )
    existing_fields: set[str] = set()
    for am in existing_assign_rx.finditer(out_mb):
        lhs = am.group(1).upper()
        rhs = am.group(2).upper()
        if lhs == rhs:
            existing_fields.add(lhs)

    new_lines: list[str] = []
    for (attr, _base_type) in plain_params:
        if attr.upper() in existing_fields:
            continue
        new_lines.append(f"  v_SearchParam.{attr} := {attr};")

    if not new_lines:
        return text, False

    insert_text = "\n" + "\n".join(new_lines)
    out_mb2 = out_mb[:insert_pos] + insert_text + out_mb[insert_pos:]
    if out_mb2 == out_mb:
        return text, False

    out_text = text.replace(mb, out_mb2)
    return out_text, True


# ------------------------------------------------------------------------------------
# LS patch per GetList/GetRecord/GetRow (SearchParam conversion)
# ------------------------------------------------------------------------------------
def _ls_init_block_for_method(method_upper: str, table_upper: str, null_list: str) -> str:
    if method_upper == f"GETLIST{table_upper}":
        return (
            f"v_SearchParam:= {table_upper}_SEARCH_PARAM( {null_list}); \n"
            f"  v_SearchParam.pFilter      := p_Filter;\n"
            f"  v_SearchParam.pOrderByCond := p_OrderByCond;\n"
            f"  v_SearchParam.pScopeName   := p_ScopeName;\n"
        )
    return (
        f"v_SearchParam:= {table_upper}_SEARCH_PARAM( {null_list}); \n"
        f"  v_SearchParam.pFilter      := p_Filter;\n"
        f"  v_SearchParam.pScopeName   := p_ScopeName;\n"
    )


def _ls_call_tail_rule_for_method(method_upper: str, table_upper: str) -> tuple[list[str], list[str]]:
    if method_upper == f"GETLIST{table_upper}":
        return (
            ["P_WITHACTIONINFO", "P_FILTER", "P_ORDERBYCOND", "P_SCOPENAME"],
            ["v_SearchParam", "p_WithActionInfo"],
        )
    if method_upper == f"GETRECORD{table_upper}":
        return (
            ["0", "P_FILTER", "NULL", "P_SCOPENAME"],
            ["v_SearchParam", "0"],
        )
    return (
        ["P_WITHACTIONINFO", "P_FILTER", "NULL", "P_SCOPENAME"],
        ["v_SearchParam", "p_WithActionInfo", "p_InvokeFromWR"],
    )


def _rebuild_getsql_args_signature_based(
    args: list[str],
    sig_order: list[str],
    me_params: list[str],
) -> list[str] | None:
    """Rebuild GetSql call args to align with the *_Q signature.

    For each parameter positionally up to and including P_SEARCHPARAM:
      - If the signature slot is a p_ME_* name and the call passes NULL, replace with the
        actual parameter name.
      - If the signature slot is P_SEARCHPARAM and the call passes NULL (or P_SEARCHPARAM),
        replace with v_SearchParam.

    Arguments after P_SEARCHPARAM that correspond to the now-removed filter params
    (p_Filter, NULL for p_OrderByCond, p_ScopeName) are dropped; all others are kept.

    Returns None when the transformation cannot be applied (e.g. signature has no
    P_SEARCHPARAM or the call is too short for the prefix to be matched).
    """
    if not sig_order:
        return None

    try:
        idx_sp_sig = sig_order.index("P_SEARCHPARAM")
    except ValueError:
        return None

    # The call must supply at least the prefix positions (0..idx_sp_sig).
    if len(args) <= idx_sp_sig:
        return None

    # Sanity-check: the arg at the P_SEARCHPARAM slot must look like a SearchParam
    # (NULL / P_SEARCHPARAM / V_SEARCHPARAM) for the positional mapping to be valid.
    # If something else is there (e.g. P_WITHACTIONINFO), the old signature did not have
    # p_SearchParam at this position and the positional mapping does not apply.
    sp_slot_upper = args[idx_sp_sig].strip().upper()
    if sp_slot_upper not in ("NULL", "P_SEARCHPARAM", "V_SEARCHPARAM"):
        return None

    # Build a lookup from uppercase ME param name to the original-case argument name.
    me_name_map: dict[str, str] = {}
    for mp in me_params:
        # Use the raw param text to preserve original capitalization (p_ME_Xxx, not P_ME_XXX).
        s = _clean_param_for_compare(mp)
        m_name = re.match(r"(?s)^\s*([A-Za-z0-9_#$]+)\b", s)
        if m_name:
            original = m_name.group(1)
            me_name_map[original.upper()] = original

    # Process prefix args (positions 0 .. idx_sp_sig inclusive).
    prefix_args = list(args[: idx_sp_sig + 1])
    for i, sig_name in enumerate(sig_order[: idx_sp_sig + 1]):
        if i >= len(prefix_args):
            break
        a_upper = prefix_args[i].strip().upper()
        if sig_name in me_name_map and a_upper == "NULL":
            prefix_args[i] = me_name_map[sig_name]
        elif sig_name == "P_SEARCHPARAM" and a_upper in ("NULL", "P_SEARCHPARAM"):
            prefix_args[i] = "v_SearchParam"

    # Process suffix args (everything after the P_SEARCHPARAM slot).
    _REMOVE_UPPER = {"P_FILTER", "P_ORDERBYCOND", "P_SCOPENAME"}
    suffix_raw = args[idx_sp_sig + 1:]
    suffix_new: list[str] = []
    for i, a in enumerate(suffix_raw):
        a_upper = a.strip().upper()
        if a_upper in _REMOVE_UPPER:
            continue
        # A lone NULL between p_Filter and p_ScopeName is the old p_OrderByCond.
        if a_upper == "NULL":
            prev_upper = suffix_raw[i - 1].strip().upper() if i > 0 else ""
            next_upper = suffix_raw[i + 1].strip().upper() if i + 1 < len(suffix_raw) else ""
            if prev_upper == "P_FILTER" or next_upper == "P_SCOPENAME":
                continue
        suffix_new.append(a)

    return prefix_args + suffix_new


def _patch_ls_method_body_for_searchparam_call(content: str, table: str, method_upper: str) -> tuple[str, bool]:
    mb = extract_method_block_body(content, "FUNCTION", method_upper)
    if not mb:
        return content, False

    changed = False
    out_mb = mb
    table_upper = table.upper()

    decl_rx = re.compile(r"(?im)^([ \t]*v_SqlText[ \t]+VARCHAR2\s*\(\s*32767\s+CHAR\s*\)\s*;[ \t]*$)")
    decl_line = f"  v_SearchParam     {table_upper}_SEARCH_PARAM;"
    if decl_line not in out_mb:
        m = decl_rx.search(out_mb)
        if m:
            out_mb2 = out_mb[:m.end()] + "\n" + decl_line + out_mb[m.end():]
            if out_mb2 != out_mb:
                out_mb = out_mb2
                changed = True

    init_anchor_rx = re.compile(r"(?im)^[ \t]*LsGeneric\.CheckParam\s*\(\s*p_Context\s*,\s*'Context'\s*\)\s*;\s*$")
    if not re.search(r"(?is)\bv_SearchParam\s*\.\s*pFilter\b", out_mb):
        nfields, null_list = _null_list_for_searchparam(table)
        if nfields > 0:
            init_block = _ls_init_block_for_method(method_upper, table_upper, null_list)
            m2 = init_anchor_rx.search(out_mb)
            if m2:
                out_mb2 = out_mb[:m2.end()] + "\n\n" + init_block + out_mb[m2.end():]
                if out_mb2 != out_mb:
                    out_mb = out_mb2
                    changed = True

    call_rx = re.compile(rf"(?is)(LSRESYNC{re.escape(table_upper)}_Q\s*\.\s*GetSql{re.escape(table_upper)}\s*\()")
    cm = call_rx.search(out_mb)
    if cm:
        span = _find_matching_paren_span(out_mb, cm.end() - 1)
        if span:
            inside = out_mb[span[0] + 1:span[1]]
            args = _split_params(inside)

            new_args = None

            # Preferred: signature-based approach using the known _Q GetSql param order.
            sig_order = _RESYNC_TABLE_GETSQL_PARAM_ORDER.get(table_upper, [])
            me_params_list = _RESYNC_TABLE_ME_PARAMS.get(table_upper, [])
            if sig_order:
                new_args = _rebuild_getsql_args_signature_based(args, sig_order, me_params_list)

            # Fallback: "last-N args" heuristic (original approach).
            if new_args is None and len(args) >= 4:
                expected_last4, replacement_tail = _ls_call_tail_rule_for_method(method_upper, table_upper)
                # Try last-5 first to cope with p_InvokeFromWR appended after the filter params.
                if len(args) >= 5:
                    tail5 = [a.strip().upper() for a in args[-5:]]
                    if tail5 == expected_last4 + ["P_INVOKEFROMWR"]:
                        new_args = args[:-5] + replacement_tail
                # Try last-4 (no trailing p_InvokeFromWR).
                if new_args is None:
                    tail4 = [a.strip().upper() for a in args[-4:]]
                    if tail4 == expected_last4:
                        new_args = args[:-4] + replacement_tail

            if new_args is not None and [a.strip() for a in new_args] != [a.strip() for a in args]:
                rebuilt_lines = [f"      {a}," for a in new_args[:-1]] + [f"      {new_args[-1]}"]
                rebuilt = "\n" + "\n".join(rebuilt_lines) + "\n"
                out_mb2 = out_mb[:span[0] + 1] + rebuilt + out_mb[span[1]:]
                if out_mb2 != out_mb:
                    out_mb = out_mb2
                    changed = True

    if not changed:
        return content, False

    return content.replace(mb, out_mb), True


def _patch_ls_pkb_getsql_calls_for_table(content: str, table: str) -> tuple[str, bool]:
    out = content
    changed_any = False
    for mname in (f"GETLIST{table.upper()}", f"GETRECORD{table.upper()}", f"GETROW{table.upper()}"):
        out2, ch = _patch_ls_method_body_for_searchparam_call(out, table, mname)
        out = out2
        changed_any = changed_any or ch
    return out, changed_any


# ------------------------------------------------------------------------------------
# Template merge
# ------------------------------------------------------------------------------------
_RESYNC_TABLE_NUMFIELDS: dict[str, int] = {}


def apply_template_merge(out_path: Path, template_path: Path, nometabella: str, prefix: str) -> tuple[int, int, int]:
    out_text, out_enc = read_text_best_effort(out_path)
    tpl_text, _ = read_text_best_effort(template_path)

    out_methods = list_methods(out_text)
    tpl_methods = list_methods(tpl_text)

    added_methods = 0
    updated_signatures = 0
    patched_bodies = 0
    blocks_to_add: list[str] = []

    for kind, tpl_name in sorted(tpl_methods):
        resolved_name = tpl_name.replace("NOMETABELLA", nometabella.upper())

        tpl_block = None
        if out_path.suffix.lower() == ".pkb":
            tpl_block = extract_method_block_body(tpl_text, kind, tpl_name)
        else:
            sm = _find_method_header_pos(tpl_text, kind, tpl_name)
            if sm:
                i = sm.start()
                j = sm.end()
                paren_depth = 0
                in_str = False
                while j < len(tpl_text):
                    ch = tpl_text[j]
                    if ch == "'":
                        if j + 1 < len(tpl_text) and tpl_text[j + 1] == "'":
                            j += 2
                            continue
                        in_str = not in_str
                        j += 1
                        continue
                    if in_str:
                        j += 1
                        continue
                    if ch == "(":
                        paren_depth += 1
                    elif ch == ")":
                        paren_depth = max(0, paren_depth - 1)
                    elif ch == ";" and paren_depth == 0:
                        tpl_block = tpl_text[i : j + 1].rstrip() + "\n"
                        break
                    j += 1

        if not tpl_block:
            continue

        tpl_block_resolved = re.sub(r"(?i)NOMETABELLA", nometabella, tpl_block)

        if (kind, resolved_name) not in out_methods:
            blocks_to_add.append(tpl_block_resolved)
            added_methods += 1
            continue

        open_tpl = _find_signature_paren_index(tpl_block_resolved, kind, resolved_name)
        if open_tpl < 0:
            continue
        span_tpl = _find_matching_paren_span(tpl_block_resolved, open_tpl)
        if not span_tpl:
            continue
        tpl_params = _split_params(tpl_block_resolved[span_tpl[0] + 1 : span_tpl[1]])

        open_out = _find_signature_paren_index(out_text, kind, resolved_name)
        if open_out < 0:
            continue
        span_out = _find_matching_paren_span(out_text, open_out)
        if not span_out:
            continue
        out_inside = out_text[span_out[0] + 1 : span_out[1]]
        out_params_before = _split_params(out_inside)

        merged_params, _added_param_names = _merge_params(tpl_params, out_params_before)

        lsresync_rule_applied = False
        if prefix.upper() == "LSRESYNC" and kind.upper() == "FUNCTION":
            merged_params, lsresync_rule_applied = _apply_lsresync_getsql_searchparam_rule(
                resolved_method_name=resolved_name,
                nometabella=nometabella,
                out_params_before_merge=out_params_before,
                merged_params=merged_params,
            )

        if prefix.upper() == "LSRESYNC" and kind.upper() == "FUNCTION" and lsresync_rule_applied:
            if any((_param_name(p) or "") == "P_SEARCHPARAM" for p in merged_params):
                _RESYNC_TABLES_WITH_SEARCHPARAM.add(nometabella.upper())
                _capture_lsresync_me_params_for_table(Path(DEST_DIR), nometabella.upper())

                # Override the param order with the complete merged order that already
                # includes p_SearchParam (the disk-based capture reads the old file which
                # had not yet been written with the SearchParam addition).
                complete_order = [(_param_name(p) or "").upper() for p in merged_params if _param_name(p)]
                _RESYNC_TABLE_GETSQL_PARAM_ORDER[nometabella.upper()] = complete_order

                # Also capture p_ME_* params directly from merged_params as a fallback
                # (in case the disk-based capture missed them).
                me_from_merge = [p.strip() for p in merged_params
                                 if (_param_name(p) or "").upper().startswith("P_ME_")]
                if me_from_merge and nometabella.upper() not in _RESYNC_TABLE_ME_PARAMS:
                    _RESYNC_TABLE_ME_PARAMS[nometabella.upper()] = me_from_merge

                num_fields, _ = _build_wherecond_blocks_for_searchparam(nometabella)
                _RESYNC_TABLE_NUMFIELDS[nometabella.upper()] = num_fields

        if [p.strip() for p in merged_params] != [p.strip() for p in out_params_before]:
            indent = "  "
            for line in out_inside.splitlines():
                if line.strip():
                    indent = re.match(r"^\s*", line).group(0)
                    break
            new_inside = _format_param_list(merged_params, indent)
            out_text = out_text[: span_out[0] + 1] + new_inside + out_text[span_out[1]:]
            updated_signatures += 1

    if blocks_to_add:
        out_text = insert_blocks_before_package_end(out_text, blocks_to_add)

    if added_methods or updated_signatures or patched_bodies:
        write_text(out_path, out_text, out_enc)

    return added_methods, updated_signatures, patched_bodies


# ------------------------------------------------------------------------------------
# Build jobs
# ------------------------------------------------------------------------------------
PREFIX_ORDER = {"LSRESYNC": 0, "LS": 1, "LSINT": 2, "LSW": 3}
EXT_ORDER = {".pks": 0, ".pkb": 1}


@dataclass(frozen=True)
class FileJob:
    in_path: Path
    prefix: str
    table: str
    ext: str

    @property
    def sort_key(self):
        return (
            self.table.upper(),
            PREFIX_ORDER.get(self.prefix.upper(), 99),
            EXT_ORDER.get(self.ext.lower(), 99),
            self.in_path.name.upper(),
        )


def _build_jobs(src_dir: Path) -> list[FileJob]:
    jobs: list[FileJob] = []
    for p in src_dir.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in EXTS:
            continue
        prefix, table = detect_prefix_and_nometabella(p.stem)
        if not prefix or not table:
            continue
        jobs.append(FileJob(in_path=p, prefix=prefix, table=table, ext=p.suffix))
    jobs.sort(key=lambda j: j.sort_key)
    return jobs


# ------------------------------------------------------------------------------------
# XML helpers
# ------------------------------------------------------------------------------------
def _tdictpackage_xml_content(package_name_no_ext_upper: str, package_name_no_ext_case: str, id_help: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<TDictPackage>\n"
        f"<Id>{package_name_no_ext_upper}</Id>\n"
        f"<PaLgName>{package_name_no_ext_upper}</PaLgName>\n"
        f"<Id_Help>{id_help}</Id_Help>\n"
        f"<SpecFileName>{{Dictionary}}\\Scripts\\Oracle\\StoredProcedures\\BO\\LocalIta\\{package_name_no_ext_case}.pks</SpecFileName>\n"
        f"<BodyFileName>{{Dictionary}}\\Scripts\\Oracle\\StoredProcedures\\BO\\LocalIta\\{package_name_no_ext_case}.pkb</BodyFileName>\n"
        "</TDictPackage>\n"
    )


def write_tdictpackage_xml_for_generated_pkg(module_dir: Path, generated_pkg_no_ext_case: str, id_help: int):
    module_dir.mkdir(parents=True, exist_ok=True)
    upper = generated_pkg_no_ext_case.upper()
    xml_name = f"TDictPackage.{upper}.1.xml"
    xml_path = module_dir / xml_name
    xml_path.write_text(
        _tdictpackage_xml_content(upper, generated_pkg_no_ext_case, id_help),
        encoding="utf-8",
    )


def reset_tdictmodule_bo_localita(dest_dir: Path):
    p = dest_dir / "TDictModule.BO_LOCALITA.1.xml"
    if p.exists():
        p.unlink()


def append_to_tdictmodule_bo_localita(dest_dir: Path, package_name_no_ext_upper: str):
    dest_dir.mkdir(parents=True, exist_ok=True)
    module_path = dest_dir / "TDictModule.BO_LOCALITA.1.xml"
    block = "<TDictPackage>\n" + f"<Id>{package_name_no_ext_upper}</Id>\n" + "</TDictPackage>\n"
    with module_path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(block)


# ------------------------------------------------------------------------------------
# main
# ------------------------------------------------------------------------------------
def main():
    src = Path(SOURCE_DIR)
    dst = Path(DEST_DIR)
    module_dir = Path(MODULE_DIR)

    if not src.exists():
        raise FileNotFoundError(f"Source dir not found: {src}")

    dst.mkdir(parents=True, exist_ok=True)
    reset_tdictmodule_bo_localita(dst)

    jobs = _build_jobs(src)

    next_help_id = MAXIDHELP + 1
    tables = sorted({j.table.upper() for j in jobs})

    completed_pairs: set[str] = set()
    generated_outputs: set[str] = set()

    for job in jobs:
        in_path = job.in_path
        prefix = job.prefix
        nometabella = job.table

        stem = in_path.stem
        out_name = f"{stem}_Q{in_path.suffix}"
        out_path = dst / out_name

        shutil.copyfile(in_path, out_path)

        raw, enc = read_text_best_effort(out_path)
        transformed = transform_content_identifiers(raw, nometabella)
        if transformed != raw:
            write_text(out_path, transformed, enc)

        template_path = find_generic_template_file(prefix, in_path.suffix)
        if template_path:
            a, u, p = apply_template_merge(out_path, template_path, nometabella, prefix)
            if a or u or p:
                print(
                    f"TEMPLATE MERGE: {out_path.name}: added={a} updated_signatures={u} patched_bodies={p}"
                )
        else:
            print(f"WARNING: template not found for prefix={prefix} ext={in_path.suffix}")

        # ----------------------------
        # LSRESYNC: multi-GetSql patching (PKS + PKB)
        # ----------------------------
        if prefix.upper() == "LSRESYNC":
            resync_text, resync_enc = read_text_best_effort(out_path)
            changed_any = False

            template_tail = []
            if template_path:
                tpl_text, _ = read_text_best_effort(template_path)
                template_tail = _extract_template_getsql_reference_params(tpl_text)

            getsql_tables = _iter_getsql_table_suffixes_in_text(resync_text)

            for t in getsql_tables:
                resync_text2, ch = _patch_lsresync_one_getsql_signature_using_template(resync_text, t, template_tail)
                if ch:
                    changed_any = True
                    resync_text = resync_text2

            if out_path.suffix.lower() == ".pkb":
                for t in getsql_tables:
                    resync_text2, ch = _patch_lsresync_replace_filter_order_scope_inside_getsql(resync_text, t)
                    if ch:
                        changed_any = True
                        resync_text = resync_text2

                for t in getsql_tables:
                    resync_text2, ch = _patch_lsresync_inject_addcond_inside_getsql(resync_text, t)
                    if ch:
                        changed_any = True
                        resync_text = resync_text2

                for t in getsql_tables:
                    resync_text2, ch = _patch_lsresync_scopes_call_inside_getsql_if_allowed(resync_text, t)
                    if ch:
                        changed_any = True
                        resync_text = resync_text2

            if changed_any:
                write_text(out_path, resync_text, resync_enc)

        # LS pkb patch: GetList/GetRecord/GetRow
        if prefix.upper() == "LS" and out_path.suffix.lower() == ".pkb":
            if nometabella.upper() in _RESYNC_TABLES_WITH_SEARCHPARAM:
                ls_text, ls_enc = read_text_best_effort(out_path)
                ls_text2, ch = _patch_ls_pkb_getsql_calls_for_table(ls_text, nometabella)
                if ch:
                    write_text(out_path, ls_text2, ls_enc)

        # CROSS injection based on LSRESYNC GetSql signature
        if nometabella.upper() in _RESYNC_TABLES_WITH_SEARCHPARAM:
            me_params = _RESYNC_TABLE_ME_PARAMS.get(nometabella.upper(), [])
            if me_params:
                if prefix.upper() == "LS":
                    if out_path.suffix.lower() == ".pks":
                        t, e = read_text_best_effort(out_path)
                        t2, ch = _inject_me_params_after_anchor_in_signature_anykind(
                            t, "FUNCTION", nometabella.upper(), me_params, anchor_param_upper="P_SEARCHPARAM"
                        )
                        # Also inject into GetRow*/GetList*/GetRecord* declarations in pks.
                        t3, ch2 = _inject_me_params_into_getrow_getrecord_getlist_signatures(
                            t2, nometabella.upper(), me_params, kind="FUNCTION"
                        )
                        if ch or ch2:
                            write_text(out_path, t3, e)
                    elif out_path.suffix.lower() == ".pkb":
                        t, e = read_text_best_effort(out_path)
                        t2, ch_sig = _inject_me_params_after_anchor_in_signature_anykind(
                            t, "FUNCTION", nometabella.upper(), me_params, anchor_param_upper="P_SEARCHPARAM"
                        )
                        t3, ch_call = _inject_me_params_into_ls_search_call_getsql_lsresync(
                            t2, nometabella.upper(), me_params
                        )
                        # Also inject into GetRow*/GetList*/GetRecord* definitions in pkb.
                        t4, ch_sig2 = _inject_me_params_into_getrow_getrecord_getlist_signatures(
                            t3, nometabella.upper(), me_params, kind="FUNCTION"
                        )
                        if ch_sig or ch_call or ch_sig2:
                            write_text(out_path, t4, e)

                if prefix.upper() == "LSINT":
                    if out_path.suffix.lower() == ".pks":
                        t, e = read_text_best_effort(out_path)
                        t2, ch = _inject_me_params_after_anchor_in_signature_anykind(
                            t, "FUNCTION", nometabella.upper(), me_params, anchor_param_upper="P_SEARCHPARAM"
                        )
                        # Also inject into GetRow*/GetList*/GetRecord* declarations in pks.
                        t3, ch2 = _inject_me_params_into_getrow_getrecord_getlist_signatures(
                            t2, nometabella.upper(), me_params, kind="FUNCTION"
                        )
                        if ch or ch2:
                            write_text(out_path, t3, e)
                    elif out_path.suffix.lower() == ".pkb":
                        t, e = read_text_best_effort(out_path)
                        t2, ch_sig = _inject_me_params_after_anchor_in_signature_anykind(
                            t, "FUNCTION", nometabella.upper(), me_params, anchor_param_upper="P_SEARCHPARAM"
                        )
                        t3, ch_call = _inject_me_params_into_ls_search_call_after_searchparam_anykind(
                            t2, "FUNCTION", nometabella.upper(), me_params, callee_package_prefix="Ls"
                        )
                        # Also inject into GetRow*/GetList*/GetRecord* definitions in pkb.
                        t4, ch_sig2 = _inject_me_params_into_getrow_getrecord_getlist_signatures(
                            t3, nometabella.upper(), me_params, kind="FUNCTION"
                        )
                        if ch_sig or ch_call or ch_sig2:
                            write_text(out_path, t4, e)

        # LsW: patch LS_SEARCH indipendente da LSRESYNC (solo in base a SEARCHOBJECTS)
        if prefix.upper() == "LSW":
            has_search = _table_has_searchobjects(nometabella.upper())

            # firma/proc LS_SEARCH_* (pks)
            if out_path.suffix.lower() == ".pks":
                t, e = read_text_best_effort(out_path)
                t2 = t
                ch_any = False

                if has_search:
                    t2, ch_sig = _patch_lsw_ls_search_signature_add_fields_from_searchparam(
                        t2, nometabella.upper()
                    )
                    ch_any = ch_any or ch_sig

                # se esiste anche il LSRESYNC per questa tabella, in _RESYNC_TABLES_WITH_SEARCHPARAM
                if nometabella.upper() in _RESYNC_TABLES_WITH_SEARCHPARAM:
                    me_params = _RESYNC_TABLE_ME_PARAMS.get(nometabella.upper(), [])
                    if me_params:
                        t2, ch_me = _inject_me_params_after_anchor_in_signature_anykind(
                            t2, "PROCEDURE", nometabella.upper(), me_params, anchor_param_upper="P_CONTEXT"
                        )
                        ch_any = ch_any or ch_me

                if ch_any:
                    write_text(out_path, t2, e)

            # body LS_SEARCH_* (pkb)
            elif out_path.suffix.lower() == ".pkb":
                t, e = read_text_best_effort(out_path)
                t2 = t
                ch_any = False

                if has_search:
                    # firma in header LS_SEARCH
                    t2, ch_sig = _patch_lsw_ls_search_signature_add_fields_from_searchparam(
                        t2, nometabella.upper()
                    )
                    ch_any = ch_any or ch_sig

                    # costruttore NULL per v_SearchParam
                    t2, cnt_ctor = patch_lsw_pkb_searchparam_constructor_null_list(
                        t2, nometabella.upper()
                    )
                    ch_any = ch_any or bool(cnt_ctor)

                    # assegnamenti v_SearchParam.CAMPO := CAMPO;
                    t2, ch_assign = _patch_lsw_pkb_searchparam_assignments(
                        t2, nometabella.upper()
                    )
                    ch_any = ch_any or ch_assign

                # se esiste LSRESYNC con p_ME_ per questa tabella, aggiungi me_params e update call
                if nometabella.upper() in _RESYNC_TABLES_WITH_SEARCHPARAM:
                    me_params = _RESYNC_TABLE_ME_PARAMS.get(nometabella.upper(), [])
                    if me_params:
                        t2, ch_me = _inject_me_params_after_anchor_in_signature_anykind(
                            t2, "PROCEDURE", nometabella.upper(), me_params, anchor_param_upper="P_CONTEXT"
                        )
                        ch_any = ch_any or ch_me

                        t2, ch_call = _inject_me_params_into_ls_search_call_after_searchparam_anykind(
                            t2, "PROCEDURE", nometabella.upper(), me_params, callee_package_prefix="LsInt"
                        )
                        ch_any = ch_any or ch_call

                if ch_any:
                    write_text(out_path, t2, e)

        print(f"OK: {in_path.name} -> {out_path.name}")

        generated_outputs.add(f"{out_path.stem}{out_path.suffix.lower()}")

        generated_stem_no_ext_case = out_path.stem
        if generated_stem_no_ext_case not in completed_pairs:
            if (f"{generated_stem_no_ext_case}.pks" in generated_outputs) and (
                f"{generated_stem_no_ext_case}.pkb" in generated_outputs
            ):
                append_to_tdictmodule_bo_localita(dst, generated_stem_no_ext_case.upper())
                completed_pairs.add(generated_stem_no_ext_case)

    for table_upper in tables:
        stems = [
            f"LSRESYNC{table_upper}_Q",
            f"LS{table_upper}_Q",
            f"LSINT{table_upper}_Q",
            f"LSW{table_upper}_Q",
        ]

        all_8_exist = True
        for s in stems:
            if not (dst / f"{s}.pks").exists():
                all_8_exist = False
            if not (dst / f"{s}.pkb").exists():
                all_8_exist = False

        if not all_8_exist:
            continue

        for s in stems:
            pks = next(
                (p for p in dst.iterdir() if p.is_file() and p.suffix.lower() == ".pks" and p.stem.upper() == s),
                None,
            )
            stem_case = pks.stem if pks else s
            write_tdictpackage_xml_for_generated_pkg(module_dir, stem_case, next_help_id)
            next_help_id += 1

    print("Done.")


if __name__ == "__main__":
    main()
