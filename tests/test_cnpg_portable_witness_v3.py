"""Typed portability retains raw custody and rejects ACL/trigger drift."""

import copy
import json

import pytest
from test_cnpg_restore_qualification import c0, witnesses
from test_cnpg_target_runtime_transition import private_pg as _private_pg

private_pg = _private_pg


def test_v3_requires_frozen_source_raw_catalog(monkeypatch):
    source, target = witnesses()
    monkeypatch.setattr(c0, "FROZEN_SOURCE_V2_CATALOG_SHA256", c0.catalog_sha256(source["raw_portable_catalog_v2"]))
    assert c0.compare(source, target)["source_raw_v2_catalog_sha256"] == c0.FROZEN_SOURCE_V2_CATALOG_SHA256
    source["raw_portable_catalog_v2"][0][2] = "e" * 64
    with pytest.raises(ValueError, match="frozen ec9"):
        c0.compare(source, target)


@pytest.mark.parametrize("field", ["raw_portable_catalog_v2", "portability_native_facts"])
def test_old_or_missing_raw_custody_never_admitted(field):
    source, target = witnesses()
    target.pop(field)
    with pytest.raises(KeyError):
        c0.checked(target, target=True)


def test_raw_v2_projection_is_retained_exactly():
    raw = c0.raw_portable_catalog_sql()
    semantic = c0.portable_catalog_sql()
    assert "acldefault" not in raw and "foreign-key-trigger" not in raw
    assert "pg_get_triggerdef(t.oid,true)" in raw
    assert "acldefault" in semantic and "foreign-key-trigger" in semantic
    assert "tgparentid" in semantic and "WITH RECURSIVE parents" in semantic
    sql = c0.emit_sql(target=True)
    assert "'raw_portable_catalog_v2'" in sql and "'portability_native_facts'" in sql
    assert sql.rstrip().endswith("COMMIT;") and "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" in sql


def projection(q):
    return json.loads(
        q(
            "SELECT jsonb_build_object('semantic',("
            + c0.portable_catalog_sql()
            + "),'raw',("
            + c0.raw_portable_catalog_sql()
            + "),'facts',("
            + c0.portability_native_facts_sql()
            + "));"
        )
    )


def target_from_projection(data, q):
    _, target = witnesses()
    target["portable_catalog"] = data["semantic"]
    target["raw_portable_catalog_v2"] = data["raw"]
    target["portability_native_facts"] = data["facts"]
    target["namespaces"] = json.loads(q("SELECT jsonb_object_agg(oid::text,nspname) FROM pg_namespace;"))
    target["roles"] = json.loads(q("SELECT jsonb_object_agg(oid::text,rolname) FROM pg_roles;"))
    # Synthetic boundary rows retain their explicit fake OID fixtures; native
    # portability rows use real private-cluster identities. No estate credit.
    target["roles"].update({"100": "synthetic_owner", "200": "synthetic_mcp"})
    return target


def entries(rows, kind, prefix):
    return [
        r
        for r in rows
        if r[0] == kind
        and ((".".join(json.loads(r[1])[:2]) if kind == "foreign-key-trigger" else r[1]).startswith(prefix))
    ]


def test_native_null_acl_and_explicit_default_equal_but_real_grants_never_equal(private_pg):
    q = private_pg
    q("CREATE TABLE portable_acl_test(id integer); CREATE SEQUENCE portable_acl_seq;")
    implicit = projection(q)
    q("GRANT ALL ON TABLE portable_acl_test TO verdify; GRANT ALL ON SEQUENCE portable_acl_seq TO verdify;")
    explicit = projection(q)
    for name in ["public.portable_acl_test", "public.portable_acl_seq"]:
        assert entries(implicit["raw"], "relation", name) != entries(explicit["raw"], "relation", name)
        assert entries(implicit["semantic"], "relation", name) == entries(explicit["semantic"], "relation", name)
    q("CREATE ROLE portable_reader; GRANT SELECT ON portable_acl_test TO portable_reader;")
    grant = projection(q)
    assert entries(grant["semantic"], "relation", "public.portable_acl_test") != entries(
        explicit["semantic"], "relation", "public.portable_acl_test"
    )
    q("GRANT SELECT ON portable_acl_test TO portable_reader WITH GRANT OPTION;")
    grant_option = projection(q)
    assert entries(grant_option["semantic"], "relation", "public.portable_acl_test") != entries(
        grant["semantic"], "relation", "public.portable_acl_test"
    )
    q("REVOKE SELECT ON portable_acl_test FROM portable_reader; REVOKE SELECT ON portable_acl_test FROM verdify;")
    revoked_owner = projection(q)
    assert entries(revoked_owner["semantic"], "relation", "public.portable_acl_test") != entries(
        implicit["semantic"], "relation", "public.portable_acl_test"
    )


def test_native_fk_oid_recreation_preserves_semantics_and_raw_facts(private_pg):
    q = private_pg
    q("CREATE TABLE portable_parent(id integer PRIMARY KEY);")
    ddl = "CREATE TABLE portable_child(id integer, CONSTRAINT parent_fk FOREIGN KEY(id) REFERENCES portable_parent(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED);"
    q(ddl)
    first = projection(q)
    q("DROP TABLE portable_child; " + ddl)
    second = projection(q)
    a = entries(first["semantic"], "foreign-key-trigger", "public.portable_")
    b = entries(second["semantic"], "foreign-key-trigger", "public.portable_")
    assert len(a) == len(b) == 4 and a == b
    assert entries(first["raw"], "trigger", "public.portable_") != entries(second["raw"], "trigger", "public.portable_")
    facts = [f for f in second["facts"]["triggers"] if f["raw_identity"].startswith("public.portable_")]
    assert len(facts) == 4 and all(f["typed_fk"] for f in facts)
    assert all(f["native"]["tgconstraint"] == f["constraint"]["oid"] for f in facts)
    assert all(f["native"]["tgfoid"] == f["function"]["oid"] for f in facts)
    # Definition and enablement remain exact semantic facts, not stripped SQL.
    q("ALTER TABLE portable_child DISABLE TRIGGER ALL;")
    disabled = projection(q)
    assert entries(disabled["semantic"], "foreign-key-trigger", "public.portable_") != b
    q("ALTER TABLE portable_child ENABLE TRIGGER ALL; DROP TABLE portable_child; " + ddl.replace("CASCADE", "RESTRICT"))
    action = projection(q)
    assert entries(action["semantic"], "foreign-key-trigger", "public.portable_") != b


def test_native_generated_looking_user_trigger_is_not_normalized(private_pg):
    q = private_pg
    q(
        "CREATE TABLE portable_user_trigger(id integer); CREATE FUNCTION portable_trigger_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$; CREATE TRIGGER harmless BEFORE INSERT ON portable_user_trigger FOR EACH ROW EXECUTE FUNCTION portable_trigger_fn();"
    )
    oid = q("SELECT oid FROM pg_trigger WHERE tgname='harmless';")
    name = "RI_ConstraintTrigger_c_" + oid
    q(f'ALTER TRIGGER harmless ON portable_user_trigger RENAME TO "{name}";')
    result = projection(q)
    assert len(entries(result["semantic"], "trigger", "public.portable_user_trigger")) == 1
    assert not entries(result["semantic"], "foreign-key-trigger", "public.portable_user_trigger")
    facts = [f for f in result["facts"]["triggers"] if f["native"]["tgname"] == name]
    assert len(facts) == 1 and facts[0]["typed_fk"] is False
    q('ALTER TRIGGER "' + name + '" ON portable_user_trigger RENAME TO another_name;')
    changed = projection(q)
    assert entries(result["semantic"], "trigger", "public.portable_user_trigger") != entries(
        changed["semantic"], "trigger", "public.portable_user_trigger"
    )


@pytest.mark.parametrize(
    "mutation", ["internal", "oid", "constraint", "function", "namespace", "language", "multiplicity"]
)
def test_typed_fk_native_proof_cannot_be_forged(private_pg, mutation):
    q = private_pg
    q(
        "CREATE TABLE portable_parent(id integer PRIMARY KEY); CREATE TABLE portable_child(id integer REFERENCES portable_parent(id));"
    )
    data = projection(q)
    target = target_from_projection(data, q)
    assert c0.checked(target, target=True)
    fact = next(f for f in target["portability_native_facts"]["triggers"] if f["typed_fk"])
    if mutation == "internal":
        fact["native"]["tgisinternal"] = False
    elif mutation == "oid":
        fact["native"]["oid"] = str(int(fact["native"]["oid"]) + 1)
    elif mutation == "constraint":
        fact["constraint"]["contype"] = "c"
    elif mutation == "function":
        fact["function"]["proname"] = "user_replacement"
    elif mutation == "namespace":
        fact["function"]["pronamespace"] = 2200
    elif mutation == "language":
        fact["language"] = "plpgsql"
    else:
        target["portable_catalog"].append(
            copy.deepcopy(next(r for r in data["semantic"] if r[0] == "foreign-key-trigger"))
        )
    with pytest.raises(ValueError):
        c0.checked(target, target=True)


@pytest.mark.parametrize("mutation", [None, "missing", "cycle"])
def test_native_partition_fk_parent_chain_is_retained_and_guarded(private_pg, mutation):
    q = private_pg
    q(
        "CREATE TABLE portable_parent(id integer PRIMARY KEY); CREATE TABLE portable_partition(id integer REFERENCES portable_parent(id)) PARTITION BY RANGE(id); CREATE TABLE portable_partition_zero PARTITION OF portable_partition FOR VALUES FROM(0) TO(10);"
    )
    data = projection(q)
    target = target_from_projection(data, q)
    assert c0.checked(target, target=True)
    fact = next(f for f in data["facts"]["triggers"] if f["typed_fk"] and f["native"]["tgparentid"] != "0")
    if mutation is None:
        assert c0.checked(target, target=True)
    else:
        fact["native"]["tgparentid"] = "999999999" if mutation == "missing" else fact["native"]["oid"]
        with pytest.raises(ValueError, match="parent custody"):
            c0.checked(target, target=True)


@pytest.mark.parametrize("mutation", ["relation_hash", "trigger_hash", "identity", "raw_definition"])
def test_semantic_projection_hashes_must_be_independently_reproduced(private_pg, mutation):
    q = private_pg
    q(
        "CREATE TABLE portable_parent(id integer PRIMARY KEY); CREATE TABLE portable_child(id integer REFERENCES portable_parent(id)); GRANT SELECT ON portable_child TO PUBLIC;"
    )
    target = target_from_projection(projection(q), q)
    assert c0.checked(target, target=True)
    if mutation == "relation_hash":
        next(r for r in target["portable_catalog"] if r[:2] == ["relation", "public.portable_child"])[2] = "0" * 64
    elif mutation == "trigger_hash":
        next(r for r in target["portable_catalog"] if r[0] == "foreign-key-trigger")[2] = "0" * 64
    elif mutation == "identity":
        next(r for r in target["portable_catalog"] if r[0] == "foreign-key-trigger")[1] += "_different"
    else:
        next(f for f in target["portability_native_facts"]["relations"] if f["identity"] == "public.portable_child")[
            "raw_definition_text"
        ] = "[]"
    with pytest.raises((ValueError, IndexError)):
        c0.checked(target, target=True)
