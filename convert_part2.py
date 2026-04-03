
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
    if idx_sp is None:
        return text, False

    me_arg_names: list[str] = []
    for p in me_params:
        mm = re.match(r"(?is)^\s*([A-Z0-9_#$]+)", p.strip())
        if mm:
            me_arg_names.append(mm.group(1))

    existing_upper = {a.strip().upper() for a in args}
    to_add = [a for a in me_arg_names if a.upper() not in existing_upper]
    if not to_add:
        return text, False

    new_args = args[: idx_sp + 1] + to_add + args[idx_sp + 1:]

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
    if idx_context is None or idx_context + 1 >= len(args) or idx_searchparam is None:
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

