"""Native NULL owner ACL equivalence preserves every captured boundary fact."""

import copy
import json
from pathlib import Path

import pytest
from test_cnpg_portable_witness_v3 import projection, target_from_projection
from test_cnpg_restore_qualification import c0
from test_cnpg_target_runtime_transition import private_pg as _private_pg

private_pg = _private_pg


def actual_pairs():
    return json.loads((Path(__file__).parent / "fixtures/cnpg-boundary-default-acl.json").read_text())


def test_actual_fourteen_pairs_preserve_original_witness_fields():
    fixture = actual_pairs()
    original = copy.deepcopy(fixture)
    result = c0.default_owner_boundary_entries(fixture["snapshot"], fixture["boundary"])
    assert sorted(result) == sorted(fixture["explicit_source_entries"])
    assert len(result) == 14
    assert fixture == original


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "duplicate",
        "owner",
        "kind",
        "name",
        "schema",
        "empty_array",
        "raw_grant",
        "raw_effective",
        "raw_missing",
    ],
)
def test_null_default_requires_matching_native_and_raw_boundary_truth(tamper):
    f = actual_pairs()
    snapshot, data = f["snapshot"], f["boundary"]
    identity = data["semantic_entries"][0].split("|")[1]
    fact = next(x for x in snapshot["portability_native_facts"]["relations"] if x["identity"] == identity)
    if tamper == "missing":
        snapshot["portability_native_facts"]["relations"].remove(fact)
    elif tamper == "duplicate":
        snapshot["portability_native_facts"]["relations"].append(copy.deepcopy(fact))
    elif tamper == "owner":
        data["semantic_entries"][0] = data["semantic_entries"][0].replace("owner=verdify", "owner=postgres")
    elif tamper == "kind":
        fact["native"]["relkind"] = "S"
    elif tamper == "name":
        fact["native"]["relname"] = "other_object"
    elif tamper == "schema":
        fact["schema"] = "other_namespace"
    elif tamper == "empty_array":
        fact["native"]["relacl"] = []
    else:
        index = next(i for i, e in enumerate(data["raw_entries"]) if e.split("|")[1] == identity)
        if tamper == "raw_missing":
            data["raw_entries"].pop(index)
        elif tamper == "raw_grant":
            data["raw_entries"][index] = data["raw_entries"][index].replace("|acl=|", "|acl=16417:SELECT:t|")
        else:
            data["raw_entries"][index] = data["raw_entries"][index].replace("effective=f:", "effective=t:")
    with pytest.raises(ValueError):
        c0.default_owner_boundary_entries(snapshot, data)


@pytest.mark.parametrize("grant", ["reader:SELECT:f", "verdify:SELECT:t", "PUBLIC:UPDATE:f"])
def test_real_privilege_or_grant_option_is_never_discarded(grant):
    f = actual_pairs()
    f["boundary"]["semantic_entries"][0] = f["boundary"]["semantic_entries"][0].replace("|acl=|", "|acl=" + grant + "|")
    result = c0.default_owner_boundary_entries(f["snapshot"], f["boundary"])
    assert result[0] == f["boundary"]["semantic_entries"][0]
    assert sorted(result) != sorted(f["explicit_source_entries"])


def test_columns_and_remaining_definition_fields_are_not_normalized():
    f = actual_pairs()
    f["boundary"]["semantic_entries"][0] += "|extra-definition=changed"
    assert sorted(c0.default_owner_boundary_entries(f["snapshot"], f["boundary"])) != sorted(
        f["explicit_source_entries"]
    )


def test_native_postgres_default_owner_acl_and_actual_extra_grant(private_pg):
    q = private_pg
    q("CREATE TABLE boundary_default_fixture(id integer); CREATE SEQUENCE boundary_default_sequence;")
    data = projection(q)
    snapshot = target_from_projection(data, q)
    c0.validate_projection_facts(snapshot, {})

    def entries():
        return json.loads(
            q("""SELECT jsonb_agg(entry ORDER BY entry) FROM (
          SELECT CASE WHEN c.relkind='S' THEN
            format('sequence|public.%s|owner=%s|acl=%s|usage=f|select=f|update=f',
              c.relname,pg_get_userbyid(c.relowner),a.acl)
          ELSE format('relation|public.%s|kind=%s|owner=%s|rls=f:f|acl=%s|effective=f:f:f:f:f:f:f|columns=1:id:integer',
              c.relname,c.relkind,pg_get_userbyid(c.relowner),a.acl) END AS entry
          FROM pg_class c CROSS JOIN LATERAL (
            SELECT coalesce(string_agg(format('%s:%s:%s',pg_get_userbyid(x.grantee),x.privilege_type,x.is_grantable),','
              ORDER BY pg_get_userbyid(x.grantee),x.privilege_type),'') AS acl
            FROM aclexplode(c.relacl) x) a
          WHERE c.relnamespace='public'::regnamespace
            AND c.relname IN('boundary_default_fixture','boundary_default_sequence')) e;""")
        )

    implicit = entries()
    normalized = c0.default_owner_boundary_entries(snapshot, {"raw_entries": implicit, "semantic_entries": implicit})
    q(
        "GRANT ALL ON TABLE boundary_default_fixture TO verdify; GRANT ALL ON SEQUENCE boundary_default_sequence TO verdify;"
    )
    assert normalized == entries()
    q(
        "CREATE ROLE boundary_extra_reader; GRANT SELECT ON boundary_default_fixture TO boundary_extra_reader WITH GRANT OPTION;"
    )
    assert normalized != entries()
