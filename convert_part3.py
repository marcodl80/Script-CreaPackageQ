
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
            if len(args) >= 4:
                tail = [a.strip().upper() for a in args[-4:]]
                expected_last4, replacement_tail = _ls_call_tail_rule_for_method(method_upper, table_upper)
                if tail == expected_last4:
                    args2 = args[:-4] + replacement_tail
                    rebuilt_lines = [f"      {a}," for a in args2[:-1]] + [f"      {args2[-1]}"]
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
                        if ch:
                            write_text(out_path, t2, e)
                    elif out_path.suffix.lower() == ".pkb":
                        t, e = read_text_best_effort(out_path)
                        t2, ch_sig = _inject_me_params_after_anchor_in_signature_anykind(
                            t, "FUNCTION", nometabella.upper(), me_params, anchor_param_upper="P_SEARCHPARAM"
                        )
                        t3, ch_call = _inject_me_params_into_ls_search_call_getsql_lsresync(
                            t2, nometabella.upper(), me_params
                        )
                        if ch_sig or ch_call:
                            write_text(out_path, t3, e)

                if prefix.upper() == "LSINT":
                    if out_path.suffix.lower() == ".pks":
                        t, e = read_text_best_effort(out_path)
                        t2, ch = _inject_me_params_after_anchor_in_signature_anykind(
                            t, "FUNCTION", nometabella.upper(), me_params, anchor_param_upper="P_SEARCHPARAM"
                        )
                        if ch:
                            write_text(out_path, t2, e)
                    elif out_path.suffix.lower() == ".pkb":
                        t, e = read_text_best_effort(out_path)
                        t2, ch_sig = _inject_me_params_after_anchor_in_signature_anykind(
                            t, "FUNCTION", nometabella.upper(), me_params, anchor_param_upper="P_SEARCHPARAM"
                        )
                        t3, ch_call = _inject_me_params_into_ls_search_call_after_searchparam_anykind(
                            t2, "FUNCTION", nometabella.upper(), me_params, callee_package_prefix="Ls"
                        )
                        if ch_sig or ch_call:
                            write_text(out_path, t3, e)

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
