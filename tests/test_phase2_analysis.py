from pathlib import Path
from types import SimpleNamespace
import pytest

from app.services.structure import ANALYZER, extract_structure, mask_endpoint, relation_candidates


@pytest.mark.skipif(not ANALYZER.is_file(), reason="Build offline Roslyn tool for this integration test")
def test_roslyn_csharp_structure(tmp_path):
    path = tmp_path / "Demo.cs"
    text = "namespace Demo; public class Example : IDisposable { public int Value {get;set;} public void Run(){ Console.WriteLine(Value); } public void Dispose(){} }"
    path.write_text(text, encoding="utf-8")
    symbols, _ = extract_structure(path, text)
    assert ("namespace", "Demo") in {(s["symbol_type"], s["symbol_name"]) for s in symbols}
    assert ("class", "Example") in {(s["symbol_type"], s["symbol_name"]) for s in symbols}
    assert ("method", "Run") in {(s["symbol_type"], s["symbol_name"]) for s in symbols}


def test_php_wordpress_hooks_rest_and_metadata():
    text = "<?php namespace A; class B {} function work(){} add_action('init','work'); register_rest_route('workspace/v1','/items', ['methods'=>'GET']); update_post_meta(1,'foo',2);"
    symbols, resources = extract_structure(Path("x.php"), text)
    names = {(s["symbol_type"], s["symbol_name"]) for s in symbols}
    assert ("wordpress_hook", "init") in names
    assert ("rest_route", "workspace/v1/items") in names
    assert any(r["endpoint_masked"] == "/wp-json/workspace/v1/items" for r in resources)


def test_javascript_typescript_symbols_and_environment_name_only():
    text = "import API from './api'; export function FetchItems(){} const token=process.env.API_KEY; fetch('https://example.com/items?token=SECRET');"
    symbols, resources = extract_structure(Path("app.ts"), text)
    assert ("environment_variable", "API_KEY") in {(s["symbol_type"], s["symbol_name"]) for s in symbols}
    assert any(s["symbol_name"] == "FetchItems" for s in symbols)
    assert all("SECRET" not in str(r) for r in resources)


def test_sql_dependencies():
    symbols, _ = extract_structure(Path("schema.sql"), "CREATE TABLE persons(id INT); SELECT * FROM persons JOIN companies ON true; UPDATE persons SET id=1;")
    names = {(s["symbol_type"], s["symbol_name"]) for s in symbols}
    assert {("table", "persons"), ("select_table", "companies"), ("update_table", "persons")} <= names


def test_external_url_masking():
    assert mask_endpoint("https://user:pass@example.com/wp-json/a?token=secret#fragment") == "https://example.com/wp-json/a"


def test_exact_system_relation_is_only_candidate():
    left = SimpleNamespace(project="a", file_path="a.cs", endpoint_masked="https://example.com/wp-json/x/v1/items")
    right = SimpleNamespace(project="b", file_path="b.php", endpoint_masked="/wp-json/x/v1/items")
    result = relation_candidates([left, right])
    assert len(result) == 1 and result[0]["confidence"] == 0.8
    assert result[0]["evidence_json"]["status"] == "candidate"


def test_no_relation_from_shared_hostname_alone():
    a = SimpleNamespace(project="a", file_path="a.cs", endpoint_masked="https://example.com/one")
    b = SimpleNamespace(project="b", file_path="b.php", endpoint_masked="https://example.com/two")
    assert not relation_candidates([a, b])
