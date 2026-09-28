import json

import pytest

from one_hop import Checker, Rule, normalise, read_rules
from one_hop.cli import main


def kind(site, old, new, **kw):
    return Checker(site.url, **kw).check(Rule(old, new))


def test_one_hop_to_a_200_passes(site):
    site.routes = {"/old": (301, "/new"), "/new": (200, None)}
    r = kind(site, "/old", "/new")
    assert r.ok and r.detail == "301 -> 200"


def test_308_and_absolute_location_pass(site):
    site.routes = {"/old": (308, f"{site.url}/new"), "/new": (200, None)}
    assert kind(site, "/old", "/new").ok


def test_chain_is_reported_with_every_hop(site):
    site.routes = {"/old": (301, "/new"), "/new": (301, "/new/"), "/new/": (200, None)}
    r = kind(site, "/old", "/new")
    assert r.kind == "chain"
    assert [h.status for h in r.hops] == [301, 301, 200]


def test_loop_is_detected(site):
    site.routes = {"/a": (301, "/b"), "/b": (301, "/a")}
    r = kind(site, "/a", "/b")
    assert r.kind == "loop"


def test_self_redirect_is_a_loop(site):
    site.routes = {"/a": (301, "/a")}
    assert kind(site, "/a", "/b").kind == "loop"


def test_missing_old_url_is_not_found(site):
    assert kind(site, "/gone", "/new").kind == "not_found"


def test_destination_404_is_not_found(site):
    site.routes = {"/old": (301, "/new")}
    r = kind(site, "/old", "/new")
    assert r.kind == "not_found" and "new URL" in r.detail


def test_wrong_target(site):
    site.routes = {"/old": (301, "/elsewhere"), "/elsewhere": (200, None)}
    assert kind(site, "/old", "/new").kind == "wrong_target"


def test_trailing_slash_matters(site):
    site.routes = {"/old": (301, "/new/"), "/new/": (200, None)}
    assert kind(site, "/old", "/new").kind == "wrong_target"


def test_temporary_redirect_fails_unless_allowed(site):
    site.routes = {"/old": (302, "/new"), "/new": (200, None)}
    assert kind(site, "/old", "/new").kind == "temporary"
    assert kind(site, "/old", "/new", allow_temporary=True).ok


def test_old_url_answering_200_is_no_redirect(site):
    site.routes = {"/old": (200, None)}
    assert kind(site, "/old", "/new").kind == "no_redirect"


def test_keep_rule_wants_a_plain_200(site):
    site.routes = {"/keep": (200, None), "/moved": (301, "/x"), "/x": (200, None)}
    assert kind(site, "/keep", "").ok
    assert kind(site, "/moved", "").kind == "redirected"
    assert kind(site, "/keep", "/keep").ok  # new == old means keep


def test_redirect_without_location(site):
    site.routes = {"/old": (301, None)}
    assert kind(site, "/old", "/new").kind == "bad_status"


def test_server_error(site):
    site.routes = {"/old": (500, None)}
    assert kind(site, "/old", "/new").kind == "bad_status"


def test_too_many_hops_is_a_chain(site):
    site.routes = {f"/{i}": (301, f"/{i + 1}") for i in range(20)}
    r = kind(site, "/0", "/1", max_hops=5)
    assert r.kind == "chain" and "more than 5" in r.detail


def test_connection_refused_is_an_error():
    r = Checker("http://127.0.0.1:9").check(Rule("/old", "/new"))
    assert r.kind == "error"


def test_head_refused_falls_back_to_get(site):
    site.refuse_head = True
    site.routes = {"/old": (301, "/new"), "/new": (200, None)}
    assert kind(site, "/old", "/new").ok
    assert {m for m, _, _ in site.seen} == {"HEAD", "GET"}


def test_query_is_part_of_the_url(site):
    site.routes = {"/old?id=1": (301, "/new?id=1"), "/new?id=1": (200, None)}
    assert kind(site, "/old?id=1", "/new?id=1").ok
    assert kind(site, "/old?id=1", "/new?id=2").kind == "wrong_target"


def test_cross_host_redirect_and_credentials_stay_home(site, other_site):
    site.routes = {"/old": (301, f"{other_site.url}/new")}
    other_site.routes = {"/new": (200, None)}
    r = Checker(site.url, headers={"Authorization": "Basic c2VjcmV0"}).check(
        Rule("/old", f"{other_site.url}/new"))
    assert r.ok
    assert site.seen[0][2].get("Authorization") == "Basic c2VjcmV0"
    assert "Authorization" not in other_site.seen[0][2]


def test_path_only_ignores_host(site, other_site):
    site.routes = {"/old": (301, f"{other_site.url}/new")}
    other_site.routes = {"/new": (200, None)}
    assert kind(site, "/old", "/new").kind == "wrong_target"
    assert kind(site, "/old", "/new", path_only=True).ok


def test_normalise():
    assert normalise("HTTPS://Example.com:443") == "https://example.com/"
    assert normalise("http://a.b:8080/x?y=1#frag") == "http://a.b:8080/x?y=1"


def test_read_rules_headers_and_comments():
    rules = read_rules("# map\nsource,destination,note\n/a,/b,x\n\n/c,,keep\n")
    assert [(r.old, r.new, r.line) for r in rules] == [("/a", "/b", 3), ("/c", "", 5)]
    rules = read_rules("to,from\n/new,/old\n")
    assert (rules[0].old, rules[0].new) == ("/old", "/new")
    assert [r.old for r in read_rules("/a,/b\n/c,/d\n")] == ["/a", "/c"]


def test_read_rules_rejects_missing_column():
    with pytest.raises(ValueError):
        read_rules("old,notes\n/a,x\n")


def test_base_must_be_absolute():
    with pytest.raises(ValueError):
        Checker("example.com")


def test_cli_exit_codes_and_json(site, tmp_path, capsys):
    site.routes = {"/a": (301, "/b"), "/b": (200, None), "/c": (301, "/d"), "/d": (301, "/e"), "/e": (200, None)}
    good = tmp_path / "good.csv"
    good.write_text("old,new\n/a,/b\n")
    assert main([str(good), "--base", site.url]) == 0
    mixed = tmp_path / "mixed.csv"
    mixed.write_text("old,new\n/a,/b\n/c,/e\n")
    out = tmp_path / "r.json"
    assert main([str(mixed), "--base", site.url, "--json", str(out)]) == 1
    data = json.loads(out.read_text())
    assert data["failed"] == 1 and data["results"][1]["kind"] == "chain"
    assert "FAIL [chain] line 3: /c" in capsys.readouterr().out


def test_cli_bad_input(tmp_path):
    empty = tmp_path / "e.csv"
    empty.write_text("")
    assert main([str(empty), "--base", "https://example.com"]) == 2
    assert main([str(tmp_path / "missing.csv"), "--base", "https://example.com"]) == 2
