from llamawebui.domain.model_manifest import HubFile, group_gguf_files


def test_group_gguf_files_builds_complete_quant_group() -> None:
    files = (
        HubFile("UD-IQ4_XS/Model-UD-IQ4_XS-00003-of-00003.gguf", 30),
        HubFile("README.md", 5),
        HubFile("UD-IQ4_XS/Model-UD-IQ4_XS-00001-of-00003.gguf", 10),
        HubFile("UD-IQ4_XS/Model-UD-IQ4_XS-00002-of-00003.gguf", 20),
    )

    groups = group_gguf_files(files)

    assert len(groups) == 1
    assert groups[0].quantization == "UD-IQ4_XS"
    assert groups[0].total_size == 60
    assert groups[0].complete
    assert groups[0].files[0].path.endswith("00001-of-00003.gguf")


def test_group_gguf_files_marks_missing_shard_and_unknown_size() -> None:
    files = (
        HubFile("Model-Q8_0-00001-of-00003.gguf", 10),
        HubFile("Model-Q8_0-00003-of-00003.gguf", None),
    )

    group = group_gguf_files(files)[0]

    assert group.quantization == "Q8_0"
    assert group.total_size is None
    assert not group.complete


def test_group_gguf_files_keeps_unsharded_models_separate() -> None:
    groups = group_gguf_files(
        (HubFile("model-Q4_K_M.gguf", 42), HubFile("model-Q5_K_M.gguf", 84))
    )

    assert [group.quantization for group in groups] == ["Q4_K_M", "Q5_K_M"]
    assert all(group.complete for group in groups)