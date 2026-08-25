from atk.diff_analyzer.modules import classify, load_module_map


def test_load_module_map_missing_file(tmp_path):
    assert load_module_map(tmp_path / "nope.yaml") == {}


def test_load_module_map_reads_yaml(tmp_path):
    p = tmp_path / "modules.yaml"
    p.write_text('modules:\n  order: ["src/order/**"]\n', encoding="utf-8")
    assert load_module_map(p) == {"order": ["src/order/**"]}


def test_classify_maps_and_buckets_unmapped():
    module_map = {
        "order": ["src/order/**", "api/**/order/**"],
        "user": ["web/src/views/user/**"],
    }
    files = [
        "src/order/service.go",
        "api/src/main/java/com/x/order/Controller.java",
        "web/src/views/user/List.vue",
        "README.md",
    ]
    groups = classify(files, module_map)
    assert groups["order"] == [
        "src/order/service.go",
        "api/src/main/java/com/x/order/Controller.java",
    ]
    assert groups["user"] == ["web/src/views/user/List.vue"]
    assert groups["__unmapped__"] == ["README.md"]


def test_classify_empty_map_all_unmapped():
    groups = classify(["a.py"], {})
    assert groups == {"__unmapped__": ["a.py"]}
