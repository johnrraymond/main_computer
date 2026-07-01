from __future__ import annotations

import pytest

from main_computer.runtime_env_file import (
    RuntimeEnvFileError,
    apply_runtime_env_file,
    load_runtime_env_file,
    merged_runtime_env,
    parse_runtime_env_text,
)


def test_parse_runtime_env_text_accepts_comments_exports_and_quoted_values() -> None:
    values = parse_runtime_env_text(
        """
        # operator notes are allowed
        MAIN_COMPUTER_HUB_NETWORK=dev
        export MAIN_COMPUTER_HUB_CHAIN_RPC_URL="http://127.0.0.1:18545"
        MAIN_COMPUTER_HUB_FDB_NAMESPACE='main computer dev namespace'
        MAIN_COMPUTER_HUB_EXPECTED_ESCROW_ADDRESS=0x1111111111111111111111111111111111111111 # trailing comment
        """,
        source="hub-runtime.env",
    )

    assert values == {
        "MAIN_COMPUTER_HUB_NETWORK": "dev",
        "MAIN_COMPUTER_HUB_CHAIN_RPC_URL": "http://127.0.0.1:18545",
        "MAIN_COMPUTER_HUB_FDB_NAMESPACE": "main computer dev namespace",
        "MAIN_COMPUTER_HUB_EXPECTED_ESCROW_ADDRESS": "0x1111111111111111111111111111111111111111",
    }



def test_parse_runtime_env_text_ignores_utf8_bom_and_comment_only_files() -> None:
    assert parse_runtime_env_text("\ufeff", source="hub-runtime.env") == {}
    assert (
        parse_runtime_env_text(
            "\ufeff# operator notes are allowed\n\nMAIN_COMPUTER_HUB_NETWORK=dev\n",
            source="hub-runtime.env",
        )
        == {"MAIN_COMPUTER_HUB_NETWORK": "dev"}
    )


def test_load_runtime_env_file_accepts_powershell_utf8_bom_only_file(tmp_path) -> None:
    env_file = tmp_path / "hub-runtime.env"
    env_file.write_bytes(b"\xef\xbb\xbf")

    assert load_runtime_env_file(env_file) == {}


def test_parse_runtime_env_text_rejects_shell_commands_and_bad_keys() -> None:
    with pytest.raises(RuntimeEnvFileError):
        parse_runtime_env_text("echo no\n", source="hub-runtime.env")

    with pytest.raises(RuntimeEnvFileError):
        parse_runtime_env_text("1BAD=value\n", source="hub-runtime.env")


def test_runtime_env_file_values_override_process_defaults(tmp_path) -> None:
    env_file = tmp_path / "hub-runtime.env"
    env_file.write_text(
        "MAIN_COMPUTER_HUB_CHAIN_RPC_URL=http://127.0.0.1:18555\n"
        "MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES=true\n",
        encoding="utf-8",
    )

    loaded = load_runtime_env_file(env_file)
    merged = merged_runtime_env(
        {
            "MAIN_COMPUTER_HUB_CHAIN_RPC_URL": "http://old.example",
            "UNCHANGED": "yes",
        },
        loaded,
    )

    assert merged["MAIN_COMPUTER_HUB_CHAIN_RPC_URL"] == "http://127.0.0.1:18555"
    assert merged["MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES"] == "true"
    assert merged["UNCHANGED"] == "yes"


def test_apply_runtime_env_file_writes_target_mapping(tmp_path) -> None:
    env_file = tmp_path / "hub-runtime.env"
    env_file.write_text("MAIN_COMPUTER_HUB_NETWORK=test\n", encoding="utf-8")
    target: dict[str, str] = {}

    loaded = apply_runtime_env_file(env_file, environ=target)

    assert loaded == {"MAIN_COMPUTER_HUB_NETWORK": "test"}
    assert target == {"MAIN_COMPUTER_HUB_NETWORK": "test"}
