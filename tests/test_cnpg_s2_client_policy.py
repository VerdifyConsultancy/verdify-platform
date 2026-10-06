from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_s2_client_has_only_exact_target_postgres_and_dns_egress_no_ingress():
    path = ROOT / "deploy/k8s/cnpg/rehearsal/cluster/cnpg-s2-runtime-client-policy.yaml"
    client, server = list(yaml.safe_load_all(path.read_text()))
    selector = client["spec"]["podSelector"]["matchLabels"]
    assert selector == {
        "app.kubernetes.io/part-of": "verdify",
        "app.kubernetes.io/component": "cnpg-s2-runtime-qualification",
        "verdify.ai/qualification-target": "verdify-cnpg-s2",
    }
    assert client["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert not client["spec"].get("ingress")
    egress = client["spec"]["egress"]
    assert len(egress) == 2
    assert egress[0]["to"] == [{"podSelector": {"matchLabels": {"cnpg.io/cluster": "verdify-cnpg-s2"}}}]
    assert egress[0]["ports"] == [{"protocol": "TCP", "port": 5432}]
    assert egress[1]["to"] == [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        }
    ]
    assert sorted((p["protocol"], p["port"]) for p in egress[1]["ports"]) == [("TCP", 53), ("UDP", 53)]
    assert server["spec"]["podSelector"]["matchLabels"] == {"cnpg.io/cluster": "verdify-cnpg-s2"}
    assert server["spec"]["ingress"] == [
        {"from": [{"podSelector": {"matchLabels": selector}}], "ports": [{"protocol": "TCP", "port": 5432}]}
    ]


def test_s2_client_labels_do_not_select_broad_server_or_historical_client_policy():
    labels = {
        "app.kubernetes.io/part-of": "verdify",
        "app.kubernetes.io/component": "cnpg-s2-runtime-qualification",
        "verdify.ai/qualification-target": "verdify-cnpg-s2",
    }
    broad = yaml.safe_load(
        (ROOT / "deploy/k8s/cnpg/rehearsal/cluster/verdify-cnpg-rehearsal-isolation.yaml").read_text()
    )
    assert any(labels.get(k) != v for k, v in broad["spec"]["podSelector"]["matchLabels"].items())
    assert labels["app.kubernetes.io/component"] != "cnpg-runtime-qualification"
    deny = yaml.safe_load((ROOT / "deploy/k8s/cnpg/rehearsal/cluster/deny-all-rehearsal.yaml").read_text())
    assert deny["spec"] == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
